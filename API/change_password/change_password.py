#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""使用者修改自己的密碼。

目前 API 沒有 Token/Session（身分靠 payload 的 user_id），所以一定要驗舊密碼，
否則知道 user_id 的人就能改掉別人的密碼。

Request:  {"payload": {"user_id": "u001", "old_password": "...", "new_password": "..."}}
Response: {"status": "Success", "msg": "密碼已更新", "data": {"user_id": "u001"}}

成功時寫一筆 PASSWORD_CHANGED 到 audit_logs（不含任何密碼或雜湊）。
失敗（舊密碼錯誤等）不寫稽核——跟 login 一致，避免往雜湊鏈灌失敗紀錄。
"""

import json
import os
import sys
from pathlib import Path

import bcrypt
import pymysql
from dotenv import load_dotenv

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
from common.audit_log_service import append_audit_log

load_dotenv()
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_USER = os.getenv('DB_USER', 'vboxuser')
DB_PASS = os.getenv('DB_PASS')
DB_NAME = os.getenv('DB_NAME', 'devicemanagement')

MIN_PASSWORD_LENGTH = 8
# bcrypt 只看前 72 bytes，超過的部分改了也等於沒改
MAX_PASSWORD_BYTES = 72

sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')


def response_json(data, status_code=200):
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8\n")
    print(json.dumps(data, ensure_ascii=False))
    sys.exit()


def main():
    try:
        if os.environ.get('REQUEST_METHOD', 'POST') != 'POST':
            response_json({"status": "Error", "msg": "僅支援 POST 請求方法"}, 405)

        raw_data = sys.stdin.read()
        if not raw_data:
            response_json({"status": "Error", "msg": "無輸入資料"}, 400)

        request_data = json.loads(raw_data)
        payload = request_data.get("payload", {})
        if not isinstance(payload, dict):
            response_json({"status": "Error", "msg": "payload 必須是物件"}, 400)

        user_id = payload.get("user_id")
        old_password = payload.get("old_password")
        new_password = payload.get("new_password")

        if not all(isinstance(v, str) and v for v in (user_id, old_password, new_password)):
            response_json({"status": "Error", "msg": "欄位不齊全"}, 400)
        if len(new_password) < MIN_PASSWORD_LENGTH:
            response_json({"status": "Error", "msg": f"新密碼至少需要 {MIN_PASSWORD_LENGTH} 個字元"}, 400)
        if len(new_password.encode('utf-8')) > MAX_PASSWORD_BYTES:
            response_json({"status": "Error", "msg": f"新密碼過長（上限 {MAX_PASSWORD_BYTES} bytes）"}, 400)
        if new_password == old_password:
            response_json({"status": "Error", "msg": "新密碼不可與舊密碼相同"}, 400)

        conn = pymysql.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASS,
            database=DB_NAME, charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor, autocommit=False,
        )

        try:
            with conn.cursor() as cursor:
                # FOR UPDATE：同一帳號同時送兩次修改，第二次會用更新後的雜湊驗舊密碼
                cursor.execute(
                    "SELECT user_id, status, password_hash FROM users WHERE user_id = %s FOR UPDATE",
                    (user_id,))
                user_record = cursor.fetchone()

                # 帳號不存在與密碼錯誤回同一個訊息，不洩漏帳號是否存在
                if not user_record or not bcrypt.checkpw(old_password.encode('utf-8'),
                                                         user_record['password_hash'].encode('utf-8')):
                    conn.rollback()
                    response_json({"status": "Error", "msg": "帳號或舊密碼錯誤"}, 401)

                if user_record['status'] != 'Active':
                    conn.rollback()
                    response_json({"status": "Error", "msg": "此帳號已被系統停用，請聯絡管理員"}, 403)

                new_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                cursor.execute("UPDATE users SET password_hash = %s WHERE user_id = %s", (new_hash, user_id))

                append_audit_log(
                    cursor,
                    actor_id=user_id,
                    actor_type="USER",
                    family_id=None,
                    device_id=None,
                    action="PASSWORD_CHANGED",
                    parameters={"target_uid": user_id},
                    status="Verified",
                    decision="ALLOW",
                    reason="USER_SELF_SERVICE",
                )
            conn.commit()

            response_json({"status": "Success", "msg": "密碼已更新", "data": {"user_id": user_id}})

        except pymysql.MySQLError:
            conn.rollback()
            response_json({"status": "Error", "msg": "資料庫操作失敗"}, 500)
        finally:
            conn.close()

    except json.JSONDecodeError:
        response_json({"status": "Error", "msg": "JSON 格式錯誤"}, 400)
    except Exception as e:
        response_json({"status": "Error", "msg": "伺服器內部錯誤", "detail": str(e)}, 500)


if __name__ == "__main__":
    main()
