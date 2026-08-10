#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC1.3 - 查詢本機 Gateway 初始化狀態。

POST JSON:
{
  "payload": {
    "user_id": "uc13_admin",
    "password": "Pass12345",
    "gateway_id": "GW_..."   // 可省略，省略時使用本機 identity
  }
}

在 UC1.2 尚未正式核發 Access Token 的現況下，本 API 暫時要求密碼驗證。
"""

from __future__ import annotations

import os
import sys

from gateway_init_common import (
    ApiError,
    bootstrap_state,
    fetch_gateway_initialization,
    fetch_genesis_event,
    get_conn,
    handle_api_error,
    load_and_validate_identity,
    normalize_payload,
    require_active_user_password,
    require_admin_access_to_gateway,
    response_json,
)


def main() -> None:
    conn = None
    try:
        if str(os.environ.get("REQUEST_METHOD", "")).upper() == "OPTIONS":
            response_json({"status": "Success"}, 200)

        payload = normalize_payload(sys.stdin.read())
        user_id = str(payload.get("user_id") or "").strip()
        password = str(payload.get("password") or "")

        identity = load_and_validate_identity()
        local_gateway_id = str(identity["gateway_id"])
        gateway_id = str(payload.get("gateway_id") or local_gateway_id).strip()

        if not user_id or not password:
            raise ApiError("user_id、password 為必填", 400)
        if gateway_id != local_gateway_id:
            raise ApiError(
                "此 UC1.3 endpoint 僅能查詢目前實體 Gateway 的初始化狀態",
                403,
                {"local_gateway_id": local_gateway_id},
            )

        conn = get_conn()
        with conn.cursor() as cursor:
            require_active_user_password(cursor, user_id, password)
            gateway = fetch_gateway_initialization(cursor, gateway_id)

            if not gateway:
                response_json(
                    {
                        "status": "Success",
                        "data": {
                            "gateway_id": gateway_id,
                            "initialized": False,
                            "identity": {
                                "curve": identity.get("curve", "SECP256R1"),
                                "public_key_fingerprint": identity["public_key_fingerprint"],
                                "hardware_model": identity["hardware_model"],
                                "firmware_version": identity["firmware_version"],
                            },
                            "bootstrap": bootstrap_state(),
                            "genesis_event": None,
                        },
                    },
                    200,
                )

            require_admin_access_to_gateway(cursor, user_id, gateway)
            event = fetch_genesis_event(cursor, int(gateway["family_id"]))
            if not event:
                raise ApiError(
                    "Gateway 已初始化但缺少 SITE_GENESIS_CREATED ledger event",
                    500,
                    {"gateway_id": gateway_id, "family_id": gateway["family_id"]},
                )

            response_json(
                {
                    "status": "Success",
                    "data": {
                        "gateway_id": gateway_id,
                        "initialized": gateway.get("initialized_at") is not None,
                        "family_id": int(gateway["family_id"]),
                        "family_name": gateway["family_name"],
                        "owner_user_id": gateway["owner_user_id"],
                        "gateway_name": gateway["gateway_name"],
                        "gateway_status": gateway["status"],
                        "binding_method": gateway["binding_method"],
                        "hardware_model": gateway["hardware_model"],
                        "firmware_version": gateway["firmware_version"],
                        "public_key_fingerprint": gateway["public_key_fingerprint"],
                        "initialized_at": gateway["initialized_at"],
                        "bootstrap": bootstrap_state(),
                        "genesis_event": {
                            "event_id": event["event_id"],
                            "event_type": event["event_type"],
                            "ledger_status": event["status"],
                            "payload_hash": event["payload_hash"],
                            "ledger_reference": event.get("ledger_reference"),
                            "retry_count": event.get("retry_count"),
                            "last_error": event.get("last_error"),
                            "created_at": event.get("created_at"),
                            "confirmed_at": event.get("confirmed_at"),
                        },
                    },
                },
                200,
            )

    except ApiError as exc:
        handle_api_error(exc)
    except Exception as exc:
        response_json(
            {"status": "Error", "msg": "伺服器內部錯誤", "detail": str(exc)},
            500,
        )
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
