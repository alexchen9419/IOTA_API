#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
import pymysql
import bcrypt
import os
from dotenv import load_dotenv

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
        user_id = payload.get("user_id")
        password = payload.get("password")

        if not all([user_id, password]):
            response_json({"status": "Error", "msg": "欄位不齊全"}, 400)

        conn = pymysql.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASS,
            database=DB_NAME, charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

        try:
            with conn.cursor() as cursor:
                # --- 修正：1. SQL 語句將 role 欄位改為 status 欄位 ---
                sql_user = "SELECT id, user_id, username, status, password_hash FROM users WHERE user_id = %s"
                cursor.execute(sql_user, (user_id,))
                user_record = cursor.fetchone()

                # 密碼與存在性基本校驗
                if not user_record or not bcrypt.checkpw(password.encode('utf-8'), user_record['password_hash'].encode('utf-8')):
                    response_json({"status": "Error", "msg": "帳號或密碼錯誤"}, 401)

                # --- 新增：2. 資安防禦校驗，若全域狀態為 Disabled 則拒絕登入 ---
                if user_record['status'] != 'Active':
                    response_json({"status": "Error", "msg": "此帳號已被系統停用，請聯絡管理員"}, 403)

                # 3. 憑證與狀態接通過，從關係表 user_families 與 families 抓取該用戶加入的家庭清單
                # （此處的 uf.role 代表特定家庭內的角色，依然有效並正常保留）
                sql_families = """
                    SELECT f.id as family_id, f.family_name, uf.role as user_role
                    FROM user_families uf
                    JOIN families f ON uf.family_id = f.id
                    WHERE uf.user_id = %s
                """
                cursor.execute(sql_families, (user_id,))
                family_list = cursor.fetchall()

                # --- 修正：4. 回傳資訊將全域 role 替換為全域 status ---
                response_json({
                    "status": "Success",
                    "msg": "登入成功",
                    "data": {
                        "user_id": user_record['user_id'],
                        "username": user_record['username'],
                        "status": user_record['status'],
                        "families": family_list  # 回傳包含該用戶在各個家庭身分的陣列
                    }
                })

        finally:
            conn.close()

    except Exception as e:
        response_json({"status": "Error", "msg": "伺服器內部錯誤", "detail": str(e)}, 500)

if __name__ == "__main__":
    main()