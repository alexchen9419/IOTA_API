#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UC3.4 formal guest-token issuance API with ledger-event enqueueing."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List

import pymysql
from dotenv import load_dotenv

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from common.audit_log_service import append_audit_log
from common.ledger_event_service import enqueue_ledger_event, hash_identifier, iso_utc, resolve_gateway_id

load_dotenv()
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "vboxuser")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME", "devicemanagement")

ALLOWED_ACTIONS = {"LOCK", "UNLOCK", "ON", "OFF", "OPEN", "CLOSE", "TOGGLE", "START", "STOP"}
DEFAULT_MAX_TOKEN_MINUTES = int(os.getenv("GUEST_TOKEN_MAX_MINUTES", "1440"))

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


def parse_actions(raw: Any) -> List[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise ApiError(400, "INVALID_ALLOWED_ACTIONS", "allowed_actions must be a string or array.")
    actions = sorted({str(x).strip().upper() for x in raw if str(x).strip()})
    if not actions:
        raise ApiError(400, "INVALID_ALLOWED_ACTIONS", "allowed_actions cannot be empty.")
    invalid = [action for action in actions if action not in ALLOWED_ACTIONS]
    if invalid:
        raise ApiError(400, "UNSUPPORTED_ACTION", "One or more actions are not supported.", invalid)
    return actions


def parse_expiry(payload: Dict[str, Any], now: dt.datetime) -> dt.datetime:
    if payload.get("expires_at"):
        try:
            value = dt.datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ApiError(400, "INVALID_EXPIRES_AT", "expires_at must be ISO 8601.") from exc
        if value.tzinfo is not None:
            value = value.astimezone(dt.timezone.utc).replace(tzinfo=None)
        expires_at = value
    else:
        minutes = int(payload.get("expires_in_minutes", 10))
        if minutes <= 0:
            raise ApiError(400, "INVALID_EXPIRY", "expires_in_minutes must be greater than 0.")
        expires_at = now + dt.timedelta(minutes=minutes)

    if expires_at <= now:
        raise ApiError(400, "INVALID_EXPIRY", "expires_at must be later than valid_from.")
    if DEFAULT_MAX_TOKEN_MINUTES > 0 and expires_at > now + dt.timedelta(minutes=DEFAULT_MAX_TOKEN_MINUTES):
        raise ApiError(
            400,
            "EXPIRY_TOO_LONG",
            f"Guest token cannot exceed {DEFAULT_MAX_TOKEN_MINUTES} minutes.",
        )
    return expires_at


def require_admin(cursor, user_id: str, family_id: int) -> None:
    cursor.execute(
        "SELECT role FROM user_families WHERE user_id=%s AND family_id=%s LIMIT 1",
        (user_id, family_id),
    )
    row = cursor.fetchone()
    if not row or str(row.get("role") or "").lower() != "admin":
        raise ApiError(403, "PERMISSION_DENIED", "Only the family Admin can issue guest tokens in the current policy model.")


def main() -> None:
    conn = None
    try:
        payload = read_payload()
        created_by = str(payload.get("created_by") or payload.get("user_id") or payload.get("admin_uid") or "").strip()
        family_id_raw = payload.get("family_id")
        device_id = str(payload.get("device_id") or "").strip()
        if not created_by or family_id_raw in (None, "") or not device_id:
            raise ApiError(400, "MISSING_FIELD", "created_by/user_id, family_id and device_id are required.")

        family_id = int(family_id_raw)
        allowed_actions = parse_actions(payload.get("allowed_actions", ["UNLOCK"]))
        max_uses = int(payload.get("max_uses", 1))
        if max_uses <= 0:
            raise ApiError(400, "INVALID_MAX_USES", "max_uses must be greater than 0.")

        valid_from_db = dt.datetime.utcnow().replace(microsecond=0)
        expires_at = parse_expiry(payload, valid_from_db)
        valid_from = iso_utc(valid_from_db)
        expires_at_iso = iso_utc(expires_at)
        token_plain = f"GUEST_{secrets.token_urlsafe(24)}"
        token_id = f"GT_{dt.datetime.utcnow().strftime('%Y%m%d')}_{secrets.token_hex(8)}"
        token_hash = hashlib.sha256(token_plain.encode("utf-8")).hexdigest()

        conn = get_conn()
        with conn.cursor() as cursor:
            require_admin(cursor, created_by, family_id)
            cursor.execute(
                "SELECT device_id, family_id, gateway_id, status FROM devices WHERE device_id=%s FOR UPDATE",
                (device_id,),
            )
            device = cursor.fetchone()
            if not device:
                raise ApiError(404, "DEVICE_NOT_FOUND", "Device not found.")
            if device.get("family_id") is None or int(device["family_id"]) != family_id:
                raise ApiError(409, "DEVICE_FAMILY_MISMATCH", "Device does not belong to family_id.")
            if str(device.get("status") or "").lower() in {"revoked", "retired", "decommissioned", "disabled"}:
                raise ApiError(409, "DEVICE_INACTIVE", "Cannot issue a guest token for an inactive device.")

            cursor.execute(
                """
                INSERT INTO guest_tokens
                  (token_id, token_hash, family_id, device_id, allowed_actions,
                   expires_at, used_count, max_uses, revoked, revoked_at, revoked_by,
                   revocation_reason_code, revocation_reason_hash, created_by, created_at)
                VALUES
                  (%s, %s, %s, %s, %s, %s, 0, %s, 0, NULL, NULL, NULL, NULL, %s, %s)
                """,
                (
                    token_id,
                    token_hash,
                    family_id,
                    device_id,
                    json.dumps(allowed_actions, ensure_ascii=False, separators=(",", ":")),
                    expires_at,
                    max_uses,
                    created_by,
                    valid_from_db,
                ),
            )

            audit = append_audit_log(
                cursor,
                actor_id=created_by,
                actor_type="USER",
                family_id=family_id,
                device_id=device_id,
                action="UC3.4_GUEST_TOKEN_ISSUED",
                parameters={
                    "token_id": token_id,
                    "token_hash": token_hash,
                    "allowed_actions": allowed_actions,
                    "expires_at": expires_at_iso,
                    "max_uses": max_uses,
                },
                status="Verified",
                decision="ALLOW",
                reason="GUEST_TOKEN_ISSUED",
            )

            gateway_id = resolve_gateway_id(
                cursor,
                family_id=family_id,
                preferred_gateway_id=device.get("gateway_id"),
                device_id=device_id,
            )
            ledger_event = enqueue_ledger_event(
                cursor,
                uc_id="UC3.4",
                event_type="GUEST_TOKEN_ISSUED",
                dedup_key=f"UC3.4:GUEST_TOKEN_ISSUED:TOKEN:{token_id}",
                family_id=family_id,
                gateway_id=gateway_id,
                device_id=device_id,
                created_by=created_by,
                source="SERVER",
                timestamp=valid_from,
                actor={
                    "actor_type": "USER",
                    "actor_id_hash": hash_identifier(created_by),
                    "actor_role": "ADMIN",
                },
                payload={
                    "token_id": token_id,
                    "token_hash": f"sha256:{token_hash}",
                    "authorization_scope": [
                        {"device_id": device_id, "allowed_actions": allowed_actions}
                    ],
                    "validity": {
                        "valid_from": valid_from,
                        "expires_at": expires_at_iso,
                    },
                    "usage_limit": {
                        "max_uses": max_uses,
                        "initial_remaining_uses": max_uses,
                    },
                    "issuer_id_hash": hash_identifier(created_by),
                    "token_status": "ACTIVE",
                },
            )
            conn.commit()

        respond(
            201,
            {
                "status": "Success",
                "message": "Guest token issued and ledger event queued.",
                "data": {
                    "token_id": token_id,
                    "guest_token": token_plain,
                    "family_id": family_id,
                    "device_id": device_id,
                    "allowed_actions": allowed_actions,
                    "valid_from": valid_from,
                    "expires_at": expires_at_iso,
                    "max_uses": max_uses,
                    "audit_log": audit,
                    "ledger_event": ledger_event,
                    "note": "Plaintext guest_token is returned only in this successful issuance response.",
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
