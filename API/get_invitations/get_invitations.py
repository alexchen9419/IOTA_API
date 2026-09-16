#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC3.2 待處理邀請查詢 API

用途：
- 讓 App 的「邀請通知」分頁列出目前使用者收到、且還沒回覆的家庭邀請。
- 回傳的每一筆都帶 invitation_id，可直接交給 respond_invitation 使用。

背景：Flutter App 從一開始就在呼叫 get_invitations.py，但後端沒有這支腳本，
因此「邀請通知」永遠是空的 —— send_invitation 送得出去、受邀者卻永遠看不到，
respond_invitation 等於沒有入口。

預設只回 Pending 的邀請。需要看歷史紀錄時可傳 include_history=true，
會一併回傳 Accepted / Rejected 的紀錄（供「通知歷史」用）。

支援 GET 與 POST：

GET  /get_invitations?user_id=member001
POST /get_invitations  {"payload": {"user_id": "member001", "include_history": false}}
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

# 一次最多回傳幾筆，避免歷史紀錄累積後把回應撐爆。
MAX_ROWS = 100


def response_json(data: Dict[str, Any], status_code: int = 200) -> None:
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
    method = os.environ.get("REQUEST_METHOD", "GET").upper()

    if method == "POST":
        raw_data = sys.stdin.read()
        if not raw_data:
            return {}
        return json.loads(raw_data).get("payload", {})

    parsed = urllib.parse.parse_qs(os.environ.get("QUERY_STRING", ""))
    return {key: value[0] for key, value in parsed.items() if value}


def as_bool(value: Any, default: bool = False) -> bool:
    """GET 傳進來的是字串（'true'/'1'），POST 傳進來的是真正的 bool。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y")


def main() -> None:
    try:
        payload = read_payload()

        user_id = str(payload.get("user_id") or "").strip()
        include_history = as_bool(payload.get("include_history"), False)

        if not user_id:
            response_json({"status": "Error", "msg": "缺少必要參數(user_id)"}, 400)

        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                # 帳號必須存在，避免用這支 API 探測任意 user_id。
                cursor.execute(
                    "SELECT user_id FROM users WHERE user_id = %s", (user_id,)
                )
                if not cursor.fetchone():
                    response_json({"status": "Error", "msg": "找不到此帳號"}, 404)

                # inviter_name 走 LEFT JOIN：邀請人帳號若之後被刪除，
                # 這筆邀請仍然要看得到，不能整列消失。
                status_filter = (
                    "" if include_history else "AND fi.status = 'Pending'"
                )
                cursor.execute(
                    f"""
                    SELECT fi.id AS invitation_id,
                           fi.family_id,
                           f.family_name,
                           fi.inviter_uid,
                           COALESCE(inviter.username, fi.inviter_uid) AS inviter_name,
                           fi.role,
                           fi.status,
                           fi.created_at
                    FROM family_invitations fi
                    LEFT JOIN families f ON f.id = fi.family_id
                    LEFT JOIN users inviter ON inviter.user_id = fi.inviter_uid
                    WHERE fi.invitee_uid = %s
                    {status_filter}
                    ORDER BY fi.created_at DESC, fi.id DESC
                    LIMIT %s
                    """,
                    (user_id, MAX_ROWS),
                )
                invitations = cursor.fetchall()

                pending_count = sum(
                    1 for inv in invitations if inv["status"] == "Pending"
                )

            response_json(
                {
                    "status": "Success",
                    "msg": "查詢成功",
                    # data 直接是陣列，對齊 App 既有的 responseData['data'] 用法。
                    "data": invitations,
                    "meta": {
                        "total": len(invitations),
                        "pending_count": pending_count,
                        "include_history": include_history,
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
