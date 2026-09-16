#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC3.2 / UC3.3 場域成員清單查詢 API

用途：
- 讓 App 的「成員清單」分頁顯示指定家庭的所有成員與其角色。
- 一併回傳臨時權限欄位（start_time / end_time / max_uses），
  讓 Admin 的「編輯權限」對話框可以帶出目前設定值。

背景：Flutter App 從一開始就在呼叫 get_family_members.py，但後端沒有這支
腳本，導致成員清單分頁永遠是空的、update_member_role 也沒有入口。

權限模型：呼叫者必須是該家庭的成員（user_families 有對應列）才能查詢，
避免任意帳號列舉別人家的成員名單。角色為 Revoked 者視同無權限。
僅 Admin 能看到成員的 email / phone_number 等聯絡資訊。

支援 GET 與 POST：

GET  /get_family_members?family_id=1&user_id=admin001
POST /get_family_members  {"payload": {"family_id": 1, "user_id": "admin001"}}
"""

import json
import os
import sys
import urllib.parse
from typing import Any, Dict

import pymysql
from dotenv import load_dotenv

load_dotenv()
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "vboxuser")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME", "devicemanagement")

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

# 這些角色雖然還在 user_families 表裡，但已經失去存取權，不應該通過權限檢查。
REVOKED_ROLES = {"revoked"}


def response_json(data: Dict[str, Any], status_code: int = 200) -> None:
    """統一輸出 CGI JSON 回應。default=str 用來處理 datetime 欄位。"""
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8\n")
    print(json.dumps(data, ensure_ascii=False, default=str))
    sys.exit()


def get_conn():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def read_payload() -> Dict[str, Any]:
    """POST 從 stdin 讀 JSON body 的 payload；GET 從 QUERY_STRING 讀。"""
    method = os.environ.get("REQUEST_METHOD", "GET").upper()

    if method == "POST":
        raw_data = sys.stdin.read()
        if not raw_data:
            return {}
        return json.loads(raw_data).get("payload", {})

    parsed = urllib.parse.parse_qs(os.environ.get("QUERY_STRING", ""))
    return {key: value[0] for key, value in parsed.items() if value}


def main() -> None:
    try:
        payload = read_payload()

        family_id = payload.get("family_id")
        # 沿用其他端點的寬鬆命名：App 送 user_id，測試腳本可能送 admin_uid。
        requester_uid = str(
            payload.get("user_id") or payload.get("admin_uid") or ""
        ).strip()

        if not family_id or not requester_uid:
            response_json(
                {"status": "Error", "msg": "缺少必要參數(family_id, user_id)"}, 400
            )

        try:
            family_id = int(family_id)
        except (TypeError, ValueError):
            response_json({"status": "Error", "msg": "family_id 必須是整數"}, 400)

        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                # 1. 確認家庭存在，順便取得家庭名稱給前端顯示。
                cursor.execute(
                    "SELECT id, family_name, admin_uid FROM families WHERE id = %s",
                    (family_id,),
                )
                family = cursor.fetchone()
                if not family:
                    response_json({"status": "Error", "msg": "找不到此家庭"}, 404)

                # 2. 權限檢查：呼叫者必須是這個家庭的有效成員。
                cursor.execute(
                    "SELECT role FROM user_families WHERE family_id = %s AND user_id = %s",
                    (family_id, requester_uid),
                )
                requester = cursor.fetchone()
                if not requester or str(requester["role"]).lower() in REVOKED_ROLES:
                    response_json(
                        {"status": "Error", "msg": "權限不足：您不是此家庭的成員"}, 403
                    )

                requester_role = requester["role"]
                is_admin = str(requester_role).lower() == "admin"

                # 3. 取成員清單。聯絡資訊只給 Admin，避免一般成員互相撈到 email/電話。
                contact_columns = (
                    "u.email, u.phone_number," if is_admin else ""
                )
                cursor.execute(
                    f"""
                    SELECT uf.user_id,
                           u.username,
                           uf.role,
                           uf.status,
                           {contact_columns}
                           uf.start_time,
                           uf.end_time,
                           uf.max_uses,
                           uf.created_at AS joined_at
                    FROM user_families uf
                    JOIN users u ON u.user_id = uf.user_id
                    WHERE uf.family_id = %s
                    ORDER BY
                        CASE LOWER(uf.role)
                            WHEN 'admin' THEN 0
                            WHEN 'member' THEN 1
                            WHEN 'technician' THEN 2
                            WHEN 'sp' THEN 3
                            WHEN 'guest' THEN 4
                            ELSE 5
                        END,
                        u.username
                    """,
                    (family_id,),
                )
                members = cursor.fetchall()

                # 標記哪一位是呼叫者本人，讓 App 可以隱藏「編輯自己權限」的入口
                # （update_member_role.py 本身也有不能自我撤權的保護）。
                for member in members:
                    member["is_self"] = member["user_id"] == requester_uid
                    member["is_family_admin"] = (
                        member["user_id"] == family["admin_uid"]
                    )

            response_json(
                {
                    "status": "Success",
                    "msg": "查詢成功",
                    "data": {
                        "family_id": family["id"],
                        "family_name": family["family_name"],
                        "my_role": requester_role,
                        "members": members,
                    },
                }
            )
        finally:
            conn.close()

    except json.JSONDecodeError:
        response_json({"status": "Error", "msg": "JSON 格式錯誤"}, 400)
    except Exception as e:
        response_json(
            {"status": "Error", "msg": "伺服器內部錯誤", "detail": str(e)}, 500
        )


if __name__ == "__main__":
    main()
