#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
import pymysql
import os
import datetime
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_USER = os.getenv('DB_USER', 'vboxuser')
DB_PASS = os.getenv('DB_PASS', '82451258')
DB_NAME = os.getenv('DB_NAME', 'database02')

# 設定標準輸出編碼
sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')

def response_json(data, status_code=200):
    """統一格式化並回傳 JSON 結構的函式"""
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8\n")
    print(json.dumps(data, ensure_ascii=False))
    sys.exit()

def main():
    try:
        # 限制僅接受 POST 請求
        if os.environ.get('REQUEST_METHOD', 'GET') != 'POST':
            response_json({"status": "Error", "msg": "僅支援 POST 請求方法"}, 405)

        raw_data = sys.stdin.read()
        if not raw_data:
            response_json({"status": "Error", "msg": "無輸入資料"}, 400)
        
        request_data = json.loads(raw_data)
        payload = request_data.get("payload", {})

        user_id = payload.get("user_id")
        target_family_id = payload.get("target_family_id")

        if not all([user_id, target_family_id]):
            response_json({"status": "Error", "msg": "核心欄位(user_id, target_family_id)不齊全"}, 400)

        # 建立資料庫連線
        conn = pymysql.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASS,
            database=DB_NAME, charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

        try:
            with conn.cursor() as cursor:
                # 1. 查詢使用者在該場域的角色與時效
                sql_check_auth = """
                    SELECT role, start_time, end_time 
                    FROM user_families 
                    WHERE user_id = %s AND family_id = %s AND role != 'Revoked'
                """
                cursor.execute(sql_check_auth, (user_id, target_family_id))
                user_auth = cursor.fetchone()

                # 若查無資料，代表無權限存取該家庭
                if not user_auth:
                    response_json({"status": "Error", "msg": "無此場域的存取權限"}, 403)

                # 2. 驗證時效 (針對訪客角色)
                now = datetime.datetime.now()
                if user_auth['end_time']:
                    if now > user_auth['end_time']:
                        response_json({"status": "Error", "msg": "您的權限已過期"}, 403)
                if user_auth['start_time']:
                    if now < user_auth['start_time']:
                        response_json({"status": "Error", "msg": "您的權限尚未生效"}, 403)

                # 3. 查詢該場域的設備清單
                # 注意：資料庫需建立 devices 表來存放設備資訊
                sql_get_devices = """
                    SELECT device_id, device_name, device_type, status 
                    FROM devices 
                    WHERE family_id = %s
                """
                cursor.execute(sql_get_devices, (target_family_id,))
                devices_list = cursor.fetchall()

                # 4. 根據角色封裝前端 UI 權限開關
                role = user_auth['role']
                permissions = {
                    "can_generate_qr": role == 'Admin',
                    "can_remove_member": role == 'Admin',
                    "can_control_device": role in ['Admin', 'Member', 'Guest']
                }

                # 5. 回傳完整情境資料供前端渲染
                response_json({
                    "status": "Success",
                    "msg": "場域切換成功",
                    "data": {
                        "family_id": target_family_id,
                        "role": role,
                        "permissions": permissions,
                        "devices": devices_list
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