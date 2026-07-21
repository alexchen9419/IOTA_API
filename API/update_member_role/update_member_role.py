#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
import pymysql
import os
import paho.mqtt.publish as publish
from dotenv import load_dotenv

# 載入環境變數配置
load_dotenv()
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_USER = os.getenv('DB_USER', 'vboxuser')
DB_PASS = os.getenv('DB_PASS')
DB_NAME = os.getenv('DB_NAME', 'devicemanagement')
MQTT_HOST = os.getenv('MQTT_HOST', 'localhost')

# 確保標準輸入輸出使用 UTF-8 編碼，防止中文亂碼
sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')

def response_json(data, status_code=200):
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8\n")
    print(json.dumps(data, ensure_ascii=False))
    sys.exit()

def main():
    try:
        # 限制必須使用 POST 請求方法
        if os.environ.get('REQUEST_METHOD', 'GET') != 'POST':
            response_json({"status": "Error", "msg": "僅支援 POST 請求方法"}, 405)

        raw_data = sys.stdin.read()
        if not raw_data:
            response_json({"status": "Error", "msg": "無輸入資料"}, 400)
        
        request_data = json.loads(raw_data)
        payload = request_data.get("payload", {})

        # 接收核心前端參數
        family_id = payload.get("family_id")
        admin_uid = payload.get("admin_uid")    # 執行操作的屋主
        target_uid = payload.get("target_uid")   # 被操作的目標成員
        target_role = payload.get("target_role") # 預期變更的角色 (Member/Guest/Revoked等)
        start_time = payload.get("start_time")   # 臨時權限開始時間 (常駐成員請傳 null)
        end_time = payload.get("end_time")       # 臨時權限結束時間 (常駐成員請傳 null)
        max_uses = payload.get("max_uses")       # 最大使用次數限制 (無限次請傳 null)

        # 1. 基礎欄位防禦性校驗
        if not all([family_id, admin_uid, target_uid, target_role]):
            response_json({"status": "Error", "msg": "核心欄位不齊全"}, 400)

        # 支援的角色身分組合法性檢查
        allowed_roles = ['Admin', 'Member', 'Guest', 'Technician', 'SP', 'Revoked']
        if target_role not in allowed_roles:
            response_json({"status": "Error", "msg": f"不合法的身分組類型: {target_role}"}, 400)

        # 管理員自我保護機制：不允許自己拔除自己的 Admin 權限
        if admin_uid == target_uid and target_role != 'Admin':
            response_json({"status": "Error", "msg": "Admin 無法變更或撤銷自身的權限，請保持至少一位管理員"}, 400)

        conn = pymysql.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASS,
            database=DB_NAME, charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

        try:
            with conn.cursor() as cursor:
                # 2. ⚡ 效能優化點：改用一條 SQL 同時查詢操作者與目標者在該場域的角色，節省 50% 網路 I/O
                sql_check_local = """
                    SELECT user_id, role 
                    FROM user_families 
                    WHERE family_id = %s AND user_id IN (%s, %s)
                """
                cursor.execute(sql_check_local, (family_id, admin_uid, target_uid))
                local_records = cursor.fetchall()
                
                # 轉換為雜湊表實作 O(1) 複雜度極速查詢
                role_map = {row['user_id']: row['role'] for row in local_records}

                # 權限核心查驗：確認操作者是不是該家庭的 Admin
                if role_map.get(admin_uid) != 'Admin':
                    response_json({"status": "Error", "msg": "權限拒絕：只有該場域的 Admin 才能管理成員身分"}, 403)

                # 3. 🔍 全域資安校驗：檢查目標用戶是否「存在於全域使用者表」且「帳號狀態正常」
                sql_check_global = "SELECT status FROM users WHERE user_id = %s"
                cursor.execute(sql_check_global, (target_uid,))
                global_user = cursor.fetchone()
                
                if not global_user:
                    response_json({"status": "Error", "msg": "授權失敗：該目標帳號尚未在平台註冊"}, 404)
                
                if global_user['status'] != 'Active':
                    response_json({"status": "Error", "msg": "授權失敗：該目標帳號已被系統全域停用"}, 403)

                # 冪等性防呆：如果目標角色原本就已經是 Revoked，則不重複進行更新
                if role_map.get(target_uid) == target_role and target_role == 'Revoked':
                    response_json({"status": "Warning", "msg": "該使用者權限先前已被撤銷，無需重複操作"}, 200)

                # 4. 🦾 核心 Upsert 業務分流（實現免邀請直接授權）
                if target_role == 'Revoked':
                    # 【撤銷權限】強制將角色改為 Revoked，並將結束時間強制歸零設定為系統當前時間 (NOW)
                    upsert_sql = """
                        INSERT INTO user_families (user_id, family_id, role, end_time)
                        VALUES (%s, %s, 'Revoked', NOW())
                        ON DUPLICATE KEY UPDATE role = 'Revoked', end_time = NOW()
                    """
                    cursor.execute(upsert_sql, (target_uid, family_id))
                    action_msg = "已成功撤銷該使用者所有權限 (變更為 Revoked)"
                else:
                    # 【發行/調整身分/恢復權限】如果原先查無紀錄直接 INSERT，有舊紀錄則直接覆蓋洗白
                    upsert_sql = """
                        INSERT INTO user_families (user_id, family_id, role, start_time, end_time, max_uses)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE 
                            role = VALUES(role), 
                            start_time = VALUES(start_time), 
                            end_time = VALUES(end_time),
                            max_uses = VALUES(max_uses)
                    """
                    cursor.execute(upsert_sql, (target_uid, family_id, target_role, start_time, end_time, max_uses))
                    action_msg = f"已成功將該使用者身分更新為 {target_role}"

                conn.commit()

            # 5. 📡 實時非阻塞同步：利用 single() 將異動動態廣播至地端閘道器
            try:
                mqtt_payload = {
                    "event": "MEMBER_ROLE_CHANGED",
                    "user_data": {
                        "user_id": target_uid,
                        "role": target_role,
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
                mqtt_msg = "身分異動指令已實時同步至地端閘道器"
            except Exception as mqtt_err:
                mqtt_msg = "雲端資料庫已完成異動，但地端 MQTT 通知同步失敗"

            response_json({
                "status": "Success", 
                "msg": f"{action_msg}。{mqtt_msg}"
            })

        finally:
            conn.close()

    except Exception as e:
        response_json({"status": "Error", "msg": "伺服器內部錯誤", "detail": str(e)}, 500)

if __name__ == "__main__":
    main()