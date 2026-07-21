#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
import pymysql
import bcrypt
import os
import random
import string
import paho.mqtt.publish as publish
from dotenv import load_dotenv

load_dotenv()
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_USER = os.getenv('DB_USER', 'vboxuser')
DB_PASS = os.getenv('DB_PASS', '82451258')
DB_NAME = os.getenv('DB_NAME', 'database02')
MQTT_HOST = os.getenv('MQTT_HOST', '192.168.0.84')

sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')

def response_json(data, status_code=200):
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8\n")
    print(json.dumps(data, ensure_ascii=False))
    sys.exit()

def generate_random_string(length=8):
    letters_and_digits = string.ascii_letters + string.digits
    return ''.join(random.choice(letters_and_digits) for i in range(length))

def main():
    try:
        if os.environ.get('REQUEST_METHOD', 'GET') != 'POST':
            response_json({"status": "Error", "msg": "僅支援 POST 請求方法"}, 405)

        raw_data = sys.stdin.read()
        if not raw_data:
            response_json({"status": "Error", "msg": "無輸入資料"}, 400)
        
        request_data = json.loads(raw_data)
        payload = request_data.get("payload", {})

        family_id = payload.get("family_id")
        admin_uid = payload.get("admin_uid")
        start_time = payload.get("start_time")
        end_time = payload.get("end_time")
        max_uses = payload.get("max_uses")

        if not all([family_id, admin_uid]):
            response_json({"status": "Error", "msg": "核心欄位(family_id, admin_uid)不齊全"}, 400)

        conn = pymysql.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASS,
            database=DB_NAME, charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

        try:
            with conn.cursor() as cursor:
                # 1. 驗證發起者是否為該場域的 Admin
                sql_check_admin = "SELECT role FROM user_families WHERE family_id = %s AND user_id = %s"
                cursor.execute(sql_check_admin, (family_id, admin_uid))
                admin_record = cursor.fetchone()
                
                if not admin_record or admin_record['role'] != 'Admin':
                    response_json({"status": "Error", "msg": "權限拒絕：只有該場域的 Admin 才能產生訪客條碼"}, 403)

                # 2. 尋找閒置的訪客帳號 (限制條件: guest_開頭，且被撤銷或已過期)
                sql_find_idle = """
                    SELECT user_id 
                    FROM user_families 
                    WHERE family_id = %s 
                      AND user_id LIKE 'guest_%%' 
                      AND (role = 'Revoked' OR (end_time IS NOT NULL AND end_time < NOW()))
                    LIMIT 1
                """
                cursor.execute(sql_find_idle, (family_id,))
                idle_account = cursor.fetchone()

                # 統一產生一組全新的密碼 (無論是重用還是新建，確保舊訪客無法使用)
                plain_password = generate_random_string(10)
                hashed_password = bcrypt.hashpw(plain_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

                if idle_account:
                    # 【策略 A】：重用閒置帳號
                    guest_uid = idle_account['user_id']
                    
                    # 更新 users 表中的密碼
                    cursor.execute("UPDATE users SET password_hash = %s WHERE user_id = %s", (hashed_password, guest_uid))
                    
                    # 更新 user_families 重新賦予權限與時效
                    sql_update_family = """
                        UPDATE user_families 
                        SET role = 'Guest', start_time = %s, end_time = %s, max_uses = %s 
                        WHERE user_id = %s AND family_id = %s
                    """
                    cursor.execute(sql_update_family, (start_time, end_time, max_uses, guest_uid, family_id))
                    action_msg = f"已重用閒置訪客帳號 ({guest_uid})"
                
                else:
                    # 【策略 B】：無閒置帳號，建立全新帳號
                    guest_uid = f"guest_{generate_random_string(6)}"
                    dummy_email = f"{guest_uid}@temp.local"
                    dummy_phone = "0000000000"

                    sql_insert_user = """
                        INSERT INTO users (user_id, username, email, phone_number, password_hash)
                        VALUES (%s, %s, %s, %s, %s)
                    """
                    cursor.execute(sql_insert_user, (guest_uid, f"訪客_{guest_uid[-4:]}", dummy_email, dummy_phone, hashed_password))

                    sql_insert_family = """
                        INSERT INTO user_families (user_id, family_id, role, start_time, end_time, max_uses)
                        VALUES (%s, %s, 'Guest', %s, %s, %s)
                    """
                    cursor.execute(sql_insert_family, (guest_uid, family_id, start_time, end_time, max_uses))
                    action_msg = f"已建立全新訪客帳號 ({guest_uid})"

                conn.commit()

            # 3. 實時非阻塞同步：將異動廣播至地端閘道器
            try:
                mqtt_payload = {
                    "event": "MEMBER_ROLE_CHANGED",
                    "user_data": {
                        "user_id": guest_uid,
                        "role": "Guest",
                        "start_time": start_time,
                        "end_time": end_time,
                        "max_uses": max_uses
                    }
                }
                topic = f"home/security/gateway_{family_id}/auth_sync"
                
                publish.single(
                    topic=topic,
                    payload=json.dumps(mqtt_payload),
                    qos=1,
                    hostname=MQTT_HOST,
                    port=1883
                )
                mqtt_msg = "權限已實時同步至地端閘道器"
            except Exception as mqtt_err:
                mqtt_msg = "資料庫已建立授權，但 MQTT 通知同步失敗"

            # 4. 回傳前端產生 QR Code 所需的 URL 與明文憑證
            base_url = "https://your-domain.com/qr-control"
            control_url = f"{base_url}?uid={guest_uid}&pwd={plain_password}"

            response_json({
                "status": "Success", 
                "msg": f"{action_msg}。{mqtt_msg}",
                "data": {
                    "user_id": guest_uid,
                    "password": plain_password,
                    "control_url": control_url
                }
            })

        except pymysql.MySQLError as e:
            conn.rollback()
            response_json({"status": "Error", "msg": "資料庫操作失敗", "detail": str(e)}, 500)
        finally:
            conn.close()

    except Exception as e:
        response_json({"status": "Error", "msg": "伺服器內部錯誤", "detail": str(e)}, 500)

if __name__ == "__main__":
    main()