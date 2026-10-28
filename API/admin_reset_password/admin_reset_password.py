#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""管理員協助重設成員密碼（忘記密碼）。

成員忘記密碼時，請所在場域的 Admin 在 App 幫忙重設：後端產生一次性臨時密碼、
設定 users.must_change_password=1，成員用臨時密碼登入後 App 會強制改密碼。

Request:  {"payload": {"family_id": 12, "admin_uid": "admin001", "target_uid": "u001"}}
Response: {"status": "Success", "msg": "...", "data": {"target_uid": "u001", "temp_password": "..."}}

限制（目前沒有 Token，身分靠 payload，所以權限要收緊）：
  - admin_uid 必須是該場域的 Admin；target_uid 必須是同場域、未撤銷的成員，且不能是自己。
  - target_uid 若在**任何**場域是 Admin，一律拒絕：避免 A 家的 Admin 藉此接管
    B 家 Admin 的帳號。管理員忘記密碼走伺服器端處理。
  - 帳號已停用不可重設。
臨時密碼只出現在這次回應；DB 只存 bcrypt 雜湊。成功時寫 PASSWORD_RESET_BY_ADMIN
到 audit_logs（不含任何密碼或雜湊）。
"""

import json
from common.safe_error import public_detail
import os
import secrets
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

# 去掉容易看錯的 0/O、1/l/I，臨時密碼常常是口頭或手抄傳給成員
TEMP_PASSWORD_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789'
TEMP_PASSWORD_LENGTH = 10

sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')


def response_json(data, status_code=200):
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8\n")
    print(json.dumps(data, ensure_ascii=False))
    sys.exit()


def generate_temp_password():
    return ''.join(secrets.choice(TEMP_PASSWORD_ALPHABET) for _ in range(TEMP_PASSWORD_LENGTH))


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

        admin_uid = payload.get("admin_uid")
        target_uid = payload.get("target_uid")
        try:
            family_id = int(payload.get("family_id"))
            if family_id <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            response_json({"status": "Error", "msg": "family_id 格式錯誤"}, 400)

        if not all(isinstance(v, str) and v for v in (admin_uid, target_uid)):
            response_json({"status": "Error", "msg": "欄位不齊全"}, 400)
        if admin_uid == target_uid:
            response_json({"status": "Error", "msg": "不能重設自己的密碼，請使用「修改密碼」"}, 400)

        conn = pymysql.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASS,
            database=DB_NAME, charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor, autocommit=False,
        )

        try:
            with conn.cursor() as cursor:
                cursor.execute("SHOW COLUMNS FROM users LIKE 'must_change_password'")
                if cursor.fetchone() is None:
                    conn.rollback()
                    response_json({"status": "Error",
                                   "msg": "資料庫缺少 users.must_change_password 欄位，請先執行 "
                                          "API/migrations/2026-10-28_users_must_change_password.sql"}, 500)

                cursor.execute(
                    "SELECT role FROM user_families WHERE family_id = %s AND user_id = %s",
                    (family_id, admin_uid))
                admin_record = cursor.fetchone()
                if not admin_record or admin_record['role'] != 'Admin':
                    conn.rollback()
                    response_json({"status": "Error", "msg": "權限拒絕：只有該場域的管理員可以重設成員密碼"}, 403)

                cursor.execute(
                    "SELECT role FROM user_families WHERE family_id = %s AND user_id = %s",
                    (family_id, target_uid))
                target_member = cursor.fetchone()
                if not target_member or target_member['role'] == 'Revoked':
                    conn.rollback()
                    response_json({"status": "Error", "msg": "此帳號不是這個場域的成員"}, 404)

                cursor.execute(
                    "SELECT COUNT(*) AS n FROM user_families WHERE user_id = %s AND role = 'Admin'",
                    (target_uid,))
                if cursor.fetchone()['n'] > 0:
                    conn.rollback()
                    response_json({"status": "Error",
                                   "msg": "無法重設管理員的密碼。管理員忘記密碼請聯絡系統維運人員"}, 403)

                # FOR UPDATE：同時兩位管理員按重設，只有後一個的臨時密碼有效，不會互相覆蓋成不一致狀態
                cursor.execute(
                    "SELECT user_id, status FROM users WHERE user_id = %s FOR UPDATE", (target_uid,))
                user_record = cursor.fetchone()
                if not user_record:
                    conn.rollback()
                    response_json({"status": "Error", "msg": "此帳號不是這個場域的成員"}, 404)
                if user_record['status'] != 'Active':
                    conn.rollback()
                    response_json({"status": "Error", "msg": "此帳號已被系統停用，無法重設"}, 403)

                temp_password = generate_temp_password()
                new_hash = bcrypt.hashpw(temp_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                cursor.execute(
                    "UPDATE users SET password_hash = %s, must_change_password = 1 WHERE user_id = %s",
                    (new_hash, target_uid))

                append_audit_log(
                    cursor,
                    actor_id=admin_uid,
                    actor_type="USER",
                    family_id=family_id,
                    device_id=None,
                    action="PASSWORD_RESET_BY_ADMIN",
                    parameters={"target_uid": target_uid},
                    status="Verified",
                    decision="ALLOW",
                    reason="ADMIN_ASSISTED_RESET",
                )
            conn.commit()

            response_json({
                "status": "Success",
                "msg": "已重設密碼，請將臨時密碼交給成員，對方登入後需設定新密碼",
                "data": {"target_uid": target_uid, "temp_password": temp_password},
            })

        except pymysql.MySQLError:
            conn.rollback()
            response_json({"status": "Error", "msg": "資料庫操作失敗"}, 500)
        finally:
            conn.close()

    except json.JSONDecodeError:
        response_json({"status": "Error", "msg": "JSON 格式錯誤"}, 400)
    except Exception as e:
        response_json({"status": "Error", "msg": "伺服器內部錯誤", "detail": public_detail(e)}, 500)


if __name__ == "__main__":
    main()
