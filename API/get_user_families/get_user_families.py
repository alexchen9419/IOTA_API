#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC1.5 使用者場域清單查詢 API

用途：
- 讓 App 在「不重新登入」的情況下刷新目前使用者可存取的場域清單。

背景：Flutter App 從一開始就在呼叫 get_user_families.py，但後端沒有這支腳本。
登入時 login.py 雖然會回傳 families 陣列，但那是登入當下的快照 —— 使用者
建立新場域、接受邀請、或被 Admin 調整角色之後，清單就過期了，而 App 沒有
任何辦法刷新（總不能要求使用者重新輸入密碼）。

回傳內容比 login.py 的版本多兩項，供場域卡片顯示：
- device_count：該場域目前有效（未除役）的裝置數量
- member_count：該場域的成員數量

角色為 Revoked 的關聯不回傳 —— 使用者已經被移除權限，不該再看到該場域。

支援 GET 與 POST：

GET  /get_user_families?user_id=admin001
POST /get_user_families  {"payload": {"user_id": "admin001"}}
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

# 被撤銷權限的關聯不應該再出現在場域清單裡。
REVOKED_ROLES = ("Revoked", "revoked", "REVOKED")

# 這些狀態代表裝置已除役，不列入場域卡片的裝置數。
RETIRED_DEVICE_STATUSES = ("Revoked", "revoked", "retired", "decommissioned")


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


def main() -> None:
    try:
        payload = read_payload()
        user_id = str(payload.get("user_id") or "").strip()

        if not user_id:
            response_json({"status": "Error", "msg": "缺少必要參數(user_id)"}, 400)

        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT user_id, username, status FROM users WHERE user_id = %s",
                    (user_id,),
                )
                user = cursor.fetchone()
                if not user:
                    response_json({"status": "Error", "msg": "找不到此帳號"}, 404)

                revoked_placeholders = ", ".join(["%s"] * len(REVOKED_ROLES))
                retired_placeholders = ", ".join(
                    ["%s"] * len(RETIRED_DEVICE_STATUSES)
                )

                # 裝置數與成員數用相關子查詢算，避免 JOIN 造成的列數相乘問題。
                cursor.execute(
                    f"""
                    SELECT f.id            AS family_id,
                           f.family_name,
                           f.admin_uid,
                           uf.role         AS user_role,
                           uf.start_time,
                           uf.end_time,
                           uf.max_uses,
                           uf.created_at   AS joined_at,
                           (SELECT COUNT(*) FROM devices d
                             WHERE d.family_id = f.id
                               AND (d.status IS NULL
                                    OR d.status NOT IN ({retired_placeholders}))
                           ) AS device_count,
                           (SELECT COUNT(*) FROM user_families m
                             WHERE m.family_id = f.id
                               AND m.role NOT IN ({revoked_placeholders})
                           ) AS member_count
                    FROM user_families uf
                    JOIN families f ON f.id = uf.family_id
                    WHERE uf.user_id = %s
                      AND uf.role NOT IN ({revoked_placeholders})
                    ORDER BY f.id
                    """,
                    (
                        *RETIRED_DEVICE_STATUSES,
                        *REVOKED_ROLES,
                        user_id,
                        *REVOKED_ROLES,
                    ),
                )
                families = cursor.fetchall()

                for family in families:
                    family["is_owner"] = family["admin_uid"] == user_id

            response_json(
                {
                    "status": "Success",
                    "msg": "查詢成功",
                    # data 直接是陣列，對齊 App 既有的 responseData['data'] 用法。
                    "data": families,
                    "meta": {
                        "user_id": user["user_id"],
                        "username": user["username"],
                        "status": user["status"],
                        "total": len(families),
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
