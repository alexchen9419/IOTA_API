#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UC4.2 訪客頁（/guest）用：用 guest_token 查自己能開哪些門、還剩幾次。

訪客掃 QR 後手機上只有 token，不知道 family_id / device_id，而
control_device.py 需要這兩個欄位。這支只讀不扣次，驗證規則跟
control_device.find_valid_guest_token() 共用；真正開門仍走 control_device
（auth_type=guest_token）。

Request:  {"guest_token": "GUEST_..."}
Response: {"status": "Success", "data": {"family_id", "expires_at", "remaining_uses",
           "allowed_actions", "devices": [{"device_id", "device_name", "device_type"}]}}
"""
from __future__ import annotations

import json
from common.safe_error import public_detail
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control_device import (  # noqa: E402
    ApiError, device_is_active, dict_cursor, fetchall, find_valid_guest_token,
    get_db_connection, guest_token_actions, read_payload, respond,
)
from common.guest_token_limits import remaining_uses  # noqa: E402


def handle(payload: dict) -> dict:
    token_plain = str(payload.get("guest_token", "")).strip()
    if not token_plain:
        raise ApiError(400, "MISSING_GUEST_TOKEN", "guest_token is required.")

    conn = get_db_connection()
    cur = dict_cursor(conn)
    try:
        token = find_valid_guest_token(cur, token_plain)
        family_id = int(token["family_id"])
        token_device = token.get("device_id")
        if token_device in (None, "", "*"):
            cur.execute(
                "SELECT device_id, device_name, device_type, status FROM devices WHERE family_id=%s ORDER BY device_name",
                (family_id,),
            )
        else:
            cur.execute(
                "SELECT device_id, device_name, device_type, status FROM devices WHERE device_id=%s AND family_id=%s",
                (token_device, family_id),
            )
        devices = [
            {"device_id": d["device_id"], "device_name": d.get("device_name") or d["device_id"],
             "device_type": d.get("device_type")}
            for d in fetchall(cur) if device_is_active(d)
        ]
        conn.rollback()  # find_valid_guest_token 用了 FOR UPDATE，這裡只讀，放掉鎖
        return {
            "status": "Success",
            "data": {
                "family_id": family_id,
                # UTC，訪客頁用瀏覽器時區顯示
                "expires_at": token["expires_at"].isoformat() + "Z" if hasattr(token["expires_at"], "isoformat")
                else str(token["expires_at"]),
                "remaining_uses": remaining_uses(token.get("max_uses"), token.get("used_count")),
                "allowed_actions": sorted(guest_token_actions(token)),
                "devices": devices,
            },
        }
    finally:
        try:
            cur.close()
        finally:
            conn.close()


def main() -> None:
    try:
        if os.environ.get("REQUEST_METHOD", "POST") != "POST":
            raise ApiError(405, "METHOD_NOT_ALLOWED", "POST only.")
        respond(200, handle(read_payload()))
    except ApiError as exc:
        respond(exc.status_code, {"status": "Error", "code": exc.code, "message": exc.message})
    except json.JSONDecodeError:
        respond(400, {"status": "Error", "code": "INVALID_JSON", "message": "Request body must be valid JSON."})
    except Exception as exc:
        detail = public_detail(exc)
        respond(500, {"status": "Error", "code": "INTERNAL_ERROR", "message": "Unexpected server error.", "detail": detail})


if __name__ == "__main__":
    main()
