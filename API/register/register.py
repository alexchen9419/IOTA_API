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
        username = payload.get("username")
        password = payload.get("password")
        email = payload.get("email")
        phone_number = payload.get("phone_number")

        if not all([user_id, username, password, email, phone_number]):
            response_json({"status": "Error", "msg": "欄位不齊全"}, 400)

        conn = pymysql.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASS,
            database=DB_NAME, charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

        try:
            with conn.cursor() as cursor:
                hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
                
                # --- 修正：SQL 語句移除 role 欄位，交由資料庫預設帶入 status = 'Active' ---
                sql = """
                    INSERT INTO users (user_id, username, email, phone_number, password_hash) 
                    VALUES (%s, %s, %s, %s, %s)
                """
                cursor.execute(sql, (user_id, username, email, phone_number, hashed.decode('utf-8')))
                auto_id = cursor.lastrowid
                conn.commit()

            # --- 修正：註冊成功回傳資訊移除 role，改回傳全域 status ---
            response_json({
                "status": "Success", 
                "msg": "帳號註冊成功",
                "data": {
                    "id": auto_id,
                    "user_id": user_id,
                    "username": username,
                    "email": email,
                    "phone_number": phone_number,
                    "status": "Active"
                }
            })

        except pymysql.err.IntegrityError as e:
            if e.args[0] == 1062:
                response_json({"status": "Error", "msg": f"帳號 '{user_id}' 已被使用，請換一個。"}, 409)
            else:
                response_json({"status": "Error", "msg": "資料庫完整性錯誤", "detail": str(e)}, 400)
        
        finally:
            conn.close()

    except Exception as e:
        response_json({"status": "Error", "msg": "伺服器內部錯誤", "detail": str(e)}, 500)

if __name__ == "__main__":
    main()