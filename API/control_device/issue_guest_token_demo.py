#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC4.2 helper: issue_guest_token_demo.py

This helper is only for testing UC4.2 before the formal UC3.4 token-issuing API
is connected. It writes a hashed token into guest_tokens.

Input:
{
  "payload": {
    "created_by": "admin_001",
    "family_id": 1,
    "device_id": "ESP32_LOCK_001",
    "allowed_actions": ["UNLOCK"],
    "expires_in_minutes": 10,
    "max_uses": 1
  }
}
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import sys
import traceback
from typing import Any, Dict, List, Optional


def json_default(obj: Any) -> str:
    if isinstance(obj, (dt.datetime, dt.date)):
        return obj.isoformat(sep=" ")
    return str(obj)


def respond(status_code: int, body: Dict[str, Any]) -> None:
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8")
    print()
    print(json.dumps(body, ensure_ascii=False, default=json_default))


def read_payload() -> Dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    data = json.loads(raw)
    return data.get("payload", data) if isinstance(data, dict) else {}


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, detail: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail


def now_utc() -> dt.datetime:
    return dt.datetime.utcnow().replace(microsecond=0)


def to_json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=json_default, separators=(",", ":"))


def get_db_connection():
    host = os.getenv("DB_HOST", "localhost")
    port = int(os.getenv("DB_PORT", "3306"))
    user = os.getenv("DB_USER", os.getenv("MYSQL_USER", "vboxuser"))
    password = os.getenv("DB_PASSWORD", os.getenv("MYSQL_PASSWORD", ""))
    database = os.getenv("DB_NAME", os.getenv("MYSQL_DATABASE", "devicemanagement"))
    try:
        import mysql.connector  # type: ignore
        return mysql.connector.connect(host=host, port=port, user=user, password=password, database=database, charset="utf8mb4", autocommit=False)
    except ImportError:
        try:
            import pymysql  # type: ignore
            return pymysql.connect(host=host, port=port, user=user, password=password, database=database, charset="utf8mb4", autocommit=False, cursorclass=pymysql.cursors.DictCursor)
        except ImportError as exc:
            raise ApiError(500, "DB_DRIVER_MISSING", "Missing MySQL driver. Install mysql-connector-python or PyMySQL.", str(exc))


def dict_cursor(conn):
    try:
        return conn.cursor(dictionary=True)
    except TypeError:
        return conn.cursor()


def fetchone(cur) -> Optional[Dict[str, Any]]:
    row = cur.fetchone()
    return row if isinstance(row, dict) else None


def fetchall(cur) -> List[Dict[str, Any]]:
    return list(cur.fetchall() or [])


def table_exists(cur, table: str) -> bool:
    cur.execute("SHOW TABLES LIKE %s", (table,))
    return fetchone(cur) is not None


def get_columns(cur, table: str) -> List[str]:
    if not table_exists(cur, table):
        return []
    cur.execute(f"SHOW COLUMNS FROM `{table}`")
    return [str(row.get("Field")) for row in fetchall(cur) if row.get("Field")]


def insert_dynamic(cur, table: str, data: Dict[str, Any]) -> bool:
    cols = set(get_columns(cur, table))
    data = {k: v for k, v in data.items() if k in cols}
    if not data:
        return False
    keys = list(data.keys())
    cur.execute(
        f"INSERT INTO `{table}` ({', '.join(f'`{k}`' for k in keys)}) VALUES ({', '.join(['%s'] * len(keys))})",
        tuple(data[k] for k in keys),
    )
    return True


def parse_allowed_actions(value: Any) -> List[str]:
    if value is None:
        value = ["UNLOCK"]
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ApiError(400, "INVALID_ALLOWED_ACTIONS", "allowed_actions must be an array or string.")
    actions = sorted({str(action).strip().upper() for action in value if str(action).strip()})
    if not actions:
        raise ApiError(400, "INVALID_ALLOWED_ACTIONS", "allowed_actions cannot be empty.")
    return actions


def handle_issue(payload: Dict[str, Any]) -> Dict[str, Any]:
    for field in ["created_by", "family_id", "device_id"]:
        if payload.get(field) in (None, ""):
            raise ApiError(400, "MISSING_FIELD", f"{field} is required.")

    family_id = int(payload["family_id"])
    device_id = str(payload["device_id"])
    created_by = str(payload["created_by"])
    allowed_actions = parse_allowed_actions(payload.get("allowed_actions"))
    max_uses = int(payload.get("max_uses", 1))
    if max_uses <= 0:
        raise ApiError(400, "INVALID_MAX_USES", "max_uses must be greater than 0.")

    if payload.get("expires_at"):
        expires_at = dt.datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00")).replace(tzinfo=None)
    else:
        expires_in_minutes = int(payload.get("expires_in_minutes", 10))
        expires_at = now_utc() + dt.timedelta(minutes=expires_in_minutes)

    token_plain = str(payload.get("guest_token") or f"GUEST_{secrets.token_urlsafe(24)}")
    token_id = f"GT_{secrets.token_hex(10)}"
    token_hash = hashlib.sha256(token_plain.encode("utf-8")).hexdigest()

    conn = get_db_connection()
    cur = dict_cursor(conn)
    try:
        if not table_exists(cur, "guest_tokens"):
            raise ApiError(500, "TABLE_MISSING", "guest_tokens table does not exist. Apply uc4_1_4_2_final.sql first.")
        insert_dynamic(cur, "guest_tokens", {
            "token_id": token_id,
            "token_hash": token_hash,
            "family_id": family_id,
            "device_id": device_id,
            "allowed_actions": to_json_text(allowed_actions),
            "expires_at": expires_at,
            "max_uses": max_uses,
            "used_count": 0,
            "revoked": 0,
            "created_by": created_by,
            "created_at": now_utc(),
        })
        conn.commit()
        return {
            "status": "Success",
            "message": "Guest token issued for UC4.2 testing.",
            "data": {
                "token_id": token_id,
                "guest_token": token_plain,
                "family_id": family_id,
                "device_id": device_id,
                "allowed_actions": allowed_actions,
                "expires_at": expires_at,
                "max_uses": max_uses,
                "note": "Only this response shows the plaintext token. Database stores SHA-256 hash.",
            },
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


def main() -> None:
    try:
        respond(200, handle_issue(read_payload()))
    except ApiError as exc:
        respond(exc.status_code, {"status": "Error", "code": exc.code, "message": exc.message, "detail": exc.detail})
    except json.JSONDecodeError as exc:
        respond(400, {"status": "Error", "code": "INVALID_JSON", "message": "Request body must be valid JSON.", "detail": str(exc)})
    except Exception as exc:
        detail = traceback.format_exc() if os.getenv("DEBUG", "0") == "1" else str(exc)
        respond(500, {"status": "Error", "code": "INTERNAL_ERROR", "message": "Unexpected server error.", "detail": detail})


if __name__ == "__main__":
    main()
