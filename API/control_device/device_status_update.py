#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC4.1 / UC4.2 device_status_update.py

HTTP/CGI status callback for real ESP32 integration.
Use it when CONTROL_MODE=mqtt and ESP32/gateway worker reports the command result.

Input:
{
  "payload": {
    "command_id": "CMD_...",
    "family_id": 1,
    "device_id": "ESP32_LOCK_001",
    "status": "SUCCEEDED",
    "physical_state": "UNLOCKED",
    "battery": 87,
    "rssi": -52
  }
}
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional, Sequence


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


def unix_now() -> int:
    return int(dt.datetime.utcnow().timestamp())


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


def get_column_types(cur, table: str) -> Dict[str, str]:
    if not table_exists(cur, table):
        return {}
    cur.execute(f"SHOW COLUMNS FROM `{table}`")
    return {str(row.get("Field")): str(row.get("Type", "")).lower() for row in fetchall(cur)}


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


def update_dynamic(cur, table: str, data: Dict[str, Any], where_sql: str, where_params: Sequence[Any]) -> int:
    cols = set(get_columns(cur, table))
    data = {k: v for k, v in data.items() if k in cols}
    if not data:
        return 0
    cur.execute(f"UPDATE `{table}` SET {', '.join(f'`{k}`=%s' for k in data)} WHERE {where_sql}", tuple(data.values()) + tuple(where_params))
    return int(getattr(cur, "rowcount", 0) or 0)


def audit_timestamp_value(cur) -> Any:
    t = get_column_types(cur, "audit_logs").get("timestamp", "")
    return unix_now() if any(x in t for x in ["int", "bigint", "decimal", "double", "float"]) else now_utc()


def get_prev_hash(cur) -> str:
    cols = get_columns(cur, "audit_logs")
    if not cols:
        return "0" * 64
    hash_col = "current_hash" if "current_hash" in cols else ("hash" if "hash" in cols else None)
    if not hash_col:
        return "0" * 64
    order_col = "id" if "id" in cols else ("log_id" if "log_id" in cols else ("timestamp" if "timestamp" in cols else hash_col))
    try:
        cur.execute(f"SELECT `{hash_col}` AS h FROM `audit_logs` WHERE `{hash_col}` IS NOT NULL ORDER BY `{order_col}` DESC LIMIT 1")
        row = fetchone(cur)
        return str(row.get("h")) if row and row.get("h") else "0" * 64
    except Exception:
        return "0" * 64


def write_audit(cur, payload: Dict[str, Any], status: str, decision: str, reason: str) -> None:
    if not table_exists(cur, "audit_logs"):
        return
    prev_hash = get_prev_hash(cur)
    created_at = now_utc()
    command_id = str(payload.get("command_id") or "")
    family_id = payload.get("family_id")
    device_id = str(payload.get("device_id") or "")
    raw_text = to_json_text(payload)
    base = f"{prev_hash}|{created_at.isoformat()}|{command_id}|{family_id}|{device_id}|DEVICE_STATUS|{status}|{decision}|{reason}|{raw_text}"
    current_hash = hashlib.sha256(base.encode("utf-8")).hexdigest()
    insert_dynamic(cur, "audit_logs", {
        "command_id": command_id,
        "user_id": "ESP32",
        "actor_id": "ESP32",
        "actor_type": "DEVICE",
        "family_id": family_id,
        "device_id": device_id,
        "action": "DEVICE_STATUS_UPDATE",
        "parameters": to_json_text({}),
        "raw_data": raw_text,
        "status": status,
        "decision": decision,
        "reason": reason,
        "prev_hash": prev_hash,
        "current_hash": current_hash,
        "hash": current_hash,
        "timestamp": audit_timestamp_value(cur),
        "created_at": created_at,
    })


def normalize_status(value: Any) -> str:
    status = str(value or "").strip().upper()
    if status in {"SUCCESS", "SUCCEEDED", "OK", "DONE"}:
        return "SUCCEEDED"
    if status in {"FAIL", "FAILED", "ERROR"}:
        return "FAILED"
    if status in {"TIMEOUT"}:
        return "TIMEOUT"
    if not status:
        raise ApiError(400, "MISSING_STATUS", "status is required.")
    return status


def handle_status(payload: Dict[str, Any]) -> Dict[str, Any]:
    for field in ["family_id", "device_id", "status"]:
        if payload.get(field) in (None, ""):
            raise ApiError(400, "MISSING_FIELD", f"{field} is required.")

    family_id = int(payload["family_id"])
    device_id = str(payload["device_id"])
    command_id = str(payload.get("command_id") or "")
    status = normalize_status(payload.get("status"))
    physical_state = payload.get("physical_state")
    now = now_utc()

    conn = get_db_connection()
    cur = dict_cursor(conn)
    try:
        if command_id:
            update_dynamic(cur, "control_commands", {
                "status": status,
                "reason": "DEVICE_STATUS_CALLBACK",
                "response_payload": to_json_text(payload),
                "completed_at": now if status in {"SUCCEEDED", "FAILED", "TIMEOUT"} else None,
            }, "`command_id`=%s", (command_id,))

        # Update latest device shadow if compatible columns exist.
        device_cols = get_columns(cur, "devices")
        if device_cols:
            key_col = "device_id" if "device_id" in device_cols else ("id" if "id" in device_cols else None)
            if key_col:
                update_dynamic(cur, "devices", {
                    "physical_state": physical_state,
                    "online_status": "Online",
                    "last_seen": now,
                    "last_update": now,
                    "updated_at": now,
                    "battery": payload.get("battery"),
                    "rssi": payload.get("rssi"),
                }, f"`{key_col}`=%s", (device_id,))

        insert_dynamic(cur, "device_telemetry", {
            "family_id": family_id,
            "device_id": device_id,
            "command_id": command_id or None,
            "physical_state": physical_state,
            "status": status,
            "telemetry_data": to_json_text(payload),
            "battery": payload.get("battery"),
            "rssi": payload.get("rssi"),
            "raw_data": to_json_text(payload),
            "recorded_at": now,
            "created_at": now,
        })

        write_audit(cur, payload, status=status, decision="ALLOW" if status == "SUCCEEDED" else "DENY", reason="DEVICE_STATUS_CALLBACK")
        conn.commit()
        return {"status": "Success", "message": "Device status updated.", "data": {"command_id": command_id, "device_id": device_id, "status": status, "physical_state": physical_state}}
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
        respond(200, handle_status(read_payload()))
    except ApiError as exc:
        respond(exc.status_code, {"status": "Error", "code": exc.code, "message": exc.message, "detail": exc.detail})
    except json.JSONDecodeError as exc:
        respond(400, {"status": "Error", "code": "INVALID_JSON", "message": "Request body must be valid JSON.", "detail": str(exc)})
    except Exception as exc:
        detail = traceback.format_exc() if os.getenv("DEBUG", "0") == "1" else str(exc)
        respond(500, {"status": "Error", "code": "INTERNAL_ERROR", "message": "Unexpected server error.", "detail": detail})


if __name__ == "__main__":
    main()
