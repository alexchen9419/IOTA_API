#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UC3.5 撤銷前的清單：家庭 Admin 查看此家庭核發過的訪客通行碼。

revoke_guest_token.py 要 token_id，但 App 只在 generate_guest_qr 那次回應拿到
一次 authorization_id，之後沒有任何地方查得回來——沒有這支，撤銷端點掛了路由
App 也不知道要撤銷哪一個。

不回 token_hash 或明文 token。只讀，不寫 audit_logs（跟 get_command_status
同理：清單畫面會被反覆打開，不該往雜湊鏈灌紀錄）。

Request:  {"family_id": 12, "admin_uid": "admin001", "include_inactive": false}
Response: {"status": "Success", "data": [{"token_id", "status", "device_id", "device_name",
           "valid_from", "expires_at", "used_count", "max_uses", "remaining_uses",
           "created_by", "created_at", "revoked_at", "revoked_by"}]}
status：ACTIVE / PENDING（未生效）/ EXPIRED / USED_UP / REVOKED，只有 ACTIVE、PENDING 可撤銷。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control_device import (  # noqa: E402
    ApiError, as_int, dict_cursor, fetchall, fetchone, get_columns, get_db_connection,
    now_utc, parse_datetime, read_payload, respond,
)
from common.guest_token_limits import remaining_uses, UNLIMITED_USES  # noqa: E402

LIST_LIMIT = 100


def utc_iso(value):
    """DB 存 UTC naive，對外帶 Z，App 的 asDateTime() 才不會誤判時區。"""
    if value in (None, ""):
        return None
    return parse_datetime(value, "datetime").replace(microsecond=0).isoformat() + "Z"


def token_status(token: dict, now: dt.datetime) -> str:
    if str(token.get("revoked") or 0) in {"1", "True", "true"}:
        return "REVOKED"
    if parse_datetime(token["expires_at"], "expires_at") <= now:
        return "EXPIRED"
    if remaining_uses(token.get("max_uses"), token.get("used_count")) == 0:
        return "USED_UP"
    if token.get("valid_from") and parse_datetime(token["valid_from"], "valid_from") > now:
        return "PENDING"
    return "ACTIVE"


def handle(payload: dict) -> dict:
    family_id = as_int(payload.get("family_id"), "family_id")
    admin_uid = str(payload.get("admin_uid") or payload.get("user_id") or "").strip()
    if not admin_uid:
        raise ApiError(400, "MISSING_FIELD", "admin_uid is required.")
    include_inactive = bool(payload.get("include_inactive"))

    conn = get_db_connection()
    cur = dict_cursor(conn)
    try:
        cur.execute("SELECT role FROM user_families WHERE user_id=%s AND family_id=%s LIMIT 1",
                    (admin_uid, family_id))
        row = fetchone(cur)
        if not row or str(row.get("role") or "").lower() != "admin":
            raise ApiError(403, "PERMISSION_DENIED", "Only the family Admin can list guest tokens.")

        valid_from_sql = "t.valid_from" if "valid_from" in get_columns(cur, "guest_tokens") else "NULL"
        cur.execute(
            f"""
            SELECT t.token_id, t.device_id, d.device_name, {valid_from_sql} AS valid_from,
                   t.expires_at, t.used_count, t.max_uses, t.revoked, t.revoked_at, t.revoked_by,
                   t.created_by, t.created_at
            FROM guest_tokens t LEFT JOIN devices d ON d.device_id = t.device_id
            WHERE t.family_id=%s
            ORDER BY t.created_at DESC
            LIMIT {LIST_LIMIT}
            """,
            (family_id,),
        )
        now = now_utc()
        items = []
        for t in fetchall(cur):
            status = token_status(t, now)
            if not include_inactive and status not in {"ACTIVE", "PENDING"}:
                continue
            max_uses = int(t.get("max_uses") or 0)
            items.append({
                "token_id": t["token_id"],
                "status": status,
                "device_id": t.get("device_id"),
                "device_name": t.get("device_name"),
                "valid_from": utc_iso(t.get("valid_from")),
                "expires_at": utc_iso(t.get("expires_at")),
                "used_count": int(t.get("used_count") or 0),
                "max_uses": None if max_uses >= UNLIMITED_USES else max_uses,
                "remaining_uses": remaining_uses(t.get("max_uses"), t.get("used_count")),
                "created_by": t.get("created_by"),
                "created_at": utc_iso(t.get("created_at")),
                "revoked_at": utc_iso(t.get("revoked_at")),
                "revoked_by": t.get("revoked_by"),
            })
        return {"status": "Success", "data": items}
    finally:
        try:
            cur.close()
        finally:
            conn.close()


def main() -> None:
    try:
        respond(200, handle(read_payload()))
    except ApiError as exc:
        respond(exc.status_code, {"status": "Error", "code": exc.code, "message": exc.message, "detail": exc.detail})
    except json.JSONDecodeError:
        respond(400, {"status": "Error", "code": "INVALID_JSON", "message": "Request body must be valid JSON."})
    except Exception as exc:
        detail = traceback.format_exc() if os.getenv("DEBUG", "0") == "1" else str(exc)
        respond(500, {"status": "Error", "code": "INTERNAL_ERROR", "message": "Unexpected server error.", "detail": detail})


if __name__ == "__main__":
    main()
