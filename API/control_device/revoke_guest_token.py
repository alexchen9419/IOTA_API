#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UC3.5 revoke a guest token and enqueue GUEST_TOKEN_REVOKED."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Dict

import pymysql
from dotenv import load_dotenv

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from common.audit_log_service import append_audit_log
from common.ledger_event_service import (
    enqueue_ledger_event,
    get_ledger_event_by_dedup,
    hash_identifier,
    iso_utc,
    resolve_gateway_id,
)

load_dotenv()
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "vboxuser")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME", "devicemanagement")

if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, detail: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail


def respond(status_code: int, body: Dict[str, Any]) -> None:
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8")
    print()
    print(json.dumps(body, ensure_ascii=False, default=str))


def read_payload() -> Dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise ApiError(400, "EMPTY_BODY", "Request body is required.")
    data = json.loads(raw)
    payload = data.get("payload", data) if isinstance(data, dict) else None
    if not isinstance(payload, dict):
        raise ApiError(400, "INVALID_PAYLOAD", "payload must be an object.")
    return payload


def get_conn():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def main() -> None:
    conn = None
    try:
        payload = read_payload()
        token_id = str(payload.get("token_id") or "").strip()
        operator = str(payload.get("operator_user_id") or payload.get("user_id") or payload.get("admin_uid") or "").strip()
        requested_family_id = payload.get("family_id")
        reason_code = str(payload.get("reason_code") or "ADMIN_MANUAL_REVOCATION").strip().upper()
        reason_detail = str(payload.get("reason") or payload.get("reason_detail") or "manual revocation").strip()
        if not token_id or not operator:
            raise ApiError(400, "MISSING_FIELD", "token_id and operator_user_id/user_id are required.")

        conn = get_conn()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM guest_tokens WHERE token_id=%s FOR UPDATE", (token_id,))
            token = cursor.fetchone()
            if not token:
                raise ApiError(404, "TOKEN_NOT_FOUND", "Guest token not found.")

            family_id = int(token["family_id"])
            if requested_family_id not in (None, "") and int(requested_family_id) != family_id:
                raise ApiError(409, "TOKEN_FAMILY_MISMATCH", "family_id does not match the token scope.")

            cursor.execute(
                "SELECT role FROM user_families WHERE user_id=%s AND family_id=%s LIMIT 1",
                (operator, family_id),
            )
            role_row = cursor.fetchone()
            is_admin = bool(role_row and str(role_row.get("role") or "").lower() == "admin")
            if not is_admin:
                raise ApiError(403, "PERMISSION_DENIED", "Only a current family Admin may revoke this token.")

            dedup_key = f"UC3.5:GUEST_TOKEN_REVOKED:TOKEN:{token_id}"
            if int(token.get("revoked") or 0) == 1:
                ledger_event = get_ledger_event_by_dedup(cursor, dedup_key)
                conn.commit()
                respond(
                    200,
                    {
                        "status": "Success",
                        "message": "Guest token was already revoked; no duplicate ledger event was created.",
                        "data": {"token_id": token_id, "ledger_event": ledger_event},
                    },
                )
                return

            now_db = dt.datetime.utcnow().replace(microsecond=0)
            now_iso = iso_utc(now_db)
            expires_at = token.get("expires_at")
            if isinstance(expires_at, dt.datetime) and expires_at <= now_db:
                raise ApiError(409, "TOKEN_ALREADY_EXPIRED", "Token has already expired naturally.")
            used_count = int(token.get("used_count") or 0)
            max_uses = int(token.get("max_uses") or 0)
            if max_uses > 0 and used_count >= max_uses:
                raise ApiError(409, "TOKEN_USAGE_EXHAUSTED", "Token usage limit has already been exhausted.")

            reason_hash = hashlib.sha256(reason_detail.encode("utf-8")).hexdigest()
            cursor.execute(
                """
                UPDATE guest_tokens
                SET revoked=1, revoked_at=%s, revoked_by=%s,
                    revocation_reason_code=%s, revocation_reason_hash=%s
                WHERE token_id=%s
                """,
                (now_db, operator, reason_code, reason_hash, token_id),
            )

            device_id = str(token.get("device_id") or "") or None
            gateway_id = resolve_gateway_id(cursor, family_id=family_id, device_id=device_id)
            remaining_uses = max(max_uses - used_count, 0) if max_uses > 0 else None

            audit = append_audit_log(
                cursor,
                actor_id=operator,
                actor_type="USER",
                family_id=family_id,
                device_id=device_id,
                action="UC3.5_GUEST_TOKEN_REVOKED",
                parameters={
                    "token_id": token_id,
                    "token_hash": token.get("token_hash"),
                    "reason_code": reason_code,
                    "revoked_at": now_iso,
                    "remaining_uses": remaining_uses,
                },
                status="Verified",
                decision="ALLOW",
                reason=reason_code,
            )

            ledger_event = enqueue_ledger_event(
                cursor,
                uc_id="UC3.5",
                event_type="GUEST_TOKEN_REVOKED",
                dedup_key=dedup_key,
                family_id=family_id,
                gateway_id=gateway_id,
                device_id=device_id,
                created_by=operator,
                source="SERVER",
                timestamp=now_iso,
                actor={
                    "actor_type": "USER",
                    "actor_id_hash": hash_identifier(operator),
                    "actor_role": "ADMIN",
                },
                payload={
                    "token_id": token_id,
                    "token_hash": f"sha256:{token.get('token_hash')}",
                    "previous_status": "ACTIVE",
                    "new_status": "REVOKED",
                    "revoked_at": now_iso,
                    "remaining_uses_at_revocation": remaining_uses,
                    "revocation_reason": {
                        "reason_code": reason_code,
                        "reason_detail_hash": f"sha256:{reason_hash}",
                    },
                    "operated_by_hash": hash_identifier(operator),
                },
            )
            conn.commit()

        respond(
            200,
            {
                "status": "Success",
                "message": "Guest token revoked and ledger event queued.",
                "data": {
                    "token_id": token_id,
                    "revoked_at": now_iso,
                    "remaining_uses": remaining_uses,
                    "audit_log": audit,
                    "ledger_event": ledger_event,
                },
            },
        )
    except ApiError as exc:
        if conn:
            conn.rollback()
        respond(exc.status_code, {"status": "Error", "code": exc.code, "message": exc.message, "detail": exc.detail})
    except json.JSONDecodeError as exc:
        if conn:
            conn.rollback()
        respond(400, {"status": "Error", "code": "INVALID_JSON", "message": "Request body must be valid JSON.", "detail": str(exc)})
    except Exception as exc:
        if conn:
            conn.rollback()
        detail = traceback.format_exc() if os.getenv("DEBUG", "0") == "1" else str(exc)
        respond(500, {"status": "Error", "code": "INTERNAL_ERROR", "message": "Unexpected server error.", "detail": detail})
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    main()
