#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
import pymysql
import os
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_USER = os.getenv('DB_USER', 'vboxuser')
DB_PASS = os.getenv('DB_PASS')
DB_NAME = os.getenv('DB_NAME', 'devicemanagement')

sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')

def response_json(data, status_code=200):
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8\n")
    print(json.dumps(data, ensure_ascii=False))
    sys.exit()

def main():
    try:
        raw_data = sys.stdin.read()
        if not raw_data:
            response_json({"status": "Error", "msg": "無輸入資料"}, 400)
        
        request_data = json.loads(raw_data)
        payload = request_data.get("payload", {})

        # 接收參數
        family_id = payload.get("family_id")
        admin_uid = payload.get("admin_uid")
        invitee_uid = payload.get("invitee_uid")
        role = payload.get("role", "Guest") # 若未傳入，基於安全預設為 Guest

        # 基礎欄位驗證
        if not all([family_id, admin_uid, invitee_uid]):
            response_json({"status": "Error", "msg": "缺少必要參數(family_id, admin_uid, invitee_uid)"}, 400)

        conn = pymysql.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASS,
            database=DB_NAME, charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

        try:
            with conn.cursor() as cursor:
                # 1. 驗證發起者是否為該家庭的管理員
                cursor.execute(
                    "SELECT 1 FROM families WHERE id = %s AND admin_uid = %s", 
                    (family_id, admin_uid)
                )
                if not cursor.fetchone():
                    response_json({"status": "Error", "msg": "權限不足，您不是該家庭的管理員"}, 403)
                
                # 2. 驗證受邀者帳號是否存在
                cursor.execute(
                    "SELECT 1 FROM users WHERE user_id = %s", 
                    (invitee_uid,)
                )
                if not cursor.fetchone():
                    response_json({"status": "Error", "msg": f"找不到受邀帳號 '{invitee_uid}'"}, 404)
                
                # 3. 檢查是否已是正式成員
                cursor.execute(
                    "SELECT 1 FROM user_families WHERE user_id = %s AND family_id = %s",
                    (invitee_uid, family_id)
                )
                if cursor.fetchone():
                    response_json({"status": "Error", "msg": "該用戶已是家庭成員"}, 409)
                    
                # 4. 檢查是否已有處理中的邀請
                cursor.execute(
                    "SELECT 1 FROM family_invitations WHERE invitee_uid = %s AND family_id = %s AND status = 'Pending'",
                    (invitee_uid, family_id)
                )
                if cursor.fetchone():
                    response_json({"status": "Error", "msg": "已發送過邀請，請等待對方確認"}, 409)
                    
                # 5. 寫入邀請紀錄
                sql = """
                    INSERT INTO family_invitations (family_id, inviter_uid, invitee_uid, role, status)
                    VALUES (%s, %s, %s, %s, 'Pending')
                """
                cursor.execute(sql, (family_id, admin_uid, invitee_uid, role))
                invitation_id = cursor.lastrowid
                conn.commit()

            # 回傳成功訊息與新建立的邀請 ID
            response_json({
                "status": "Success", 
                "msg": "邀請發送成功",
                "data": {
                    "invitation_id": invitation_id,
                    "role": role
                }
            }, 201)

        except pymysql.MySQLError as e:
            conn.rollback()
            response_json({"status": "Error", "msg": "資料庫操作失敗", "detail": str(e)}, 500)
        finally:
            conn.close()

    except Exception as e:
        response_json({"status": "Error", "msg": "伺服器內部錯誤", "detail": str(e)}, 500)

if __name__ == "__main__":
    main()