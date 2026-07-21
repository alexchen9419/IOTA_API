#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC4.3 get_family_dashboard.py

UC4.3: A permitted user requests the current family/field dashboard. The API
validates the user's role in the selected family, then returns all devices in the
family with their latest physical state and connection health.

Design decisions confirmed for this version:
1. Path: api/大概率是API吧/dashboard/get_family_dashboard.py
2. Guest is not allowed to query UC4.3 dashboard.
3. Successful and denied dashboard views are written to audit_logs as
   DASHBOARD_VIEWED.
4. include_history=true can return recent telemetry records per device.

Input example:
{
  "payload": {
    "auth_type": "user",
    "user_id": "admin_001",
    "family_id": 12,
    "include_history": true,
    "history_limit": 5
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
from typing import Any, Dict, Iterable, List, Optional, Sequence


# ---------------------------------------------------------------------------
# CGI / API helpers
# ---------------------------------------------------------------------------


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
    if isinstance(data, dict) and isinstance(data.get("payload"), dict):
        return data["payload"]
    if not isinstance(data, dict):
        raise ApiError(400, "INVALID_JSON_BODY", "Request JSON must be an object.")
    return data


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, detail: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def get_db_connection():
    host = os.getenv("DB_HOST", "localhost")
    port = int(os.getenv("DB_PORT", "3306"))
    user = os.getenv("DB_USER", os.getenv("MYSQL_USER", "vboxuser"))
    password = os.getenv("DB_PASSWORD", os.getenv("MYSQL_PASSWORD", ""))
    database = os.getenv("DB_NAME", os.getenv("MYSQL_DATABASE", "devicemanagement"))

    try:
        import mysql.connector  # type: ignore

        return mysql.connector.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset="utf8mb4",
            autocommit=False,
        )
    except ImportError:
        try:
            import pymysql  # type: ignore

            return pymysql.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                charset="utf8mb4",
                autocommit=False,
                cursorclass=pymysql.cursors.DictCursor,
            )
        except ImportError as exc:
            raise ApiError(
                500,
                "DB_DRIVER_MISSING",
                "Missing MySQL driver. Install mysql-connector-python or PyMySQL.",
                str(exc),
            )


def dict_cursor(conn):
    try:
        return conn.cursor(dictionary=True)
    except TypeError:
        return conn.cursor()


def fetchone(cur) -> Optional[Dict[str, Any]]:
    row = cur.fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    raise ApiError(500, "DB_CURSOR_ERROR", "Database cursor did not return dictionaries.")


def fetchall(cur) -> List[Dict[str, Any]]:
    rows = cur.fetchall() or []
    return list(rows)


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


def to_json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=json_default, separators=(",", ":"))


def filter_by_columns(cur, table: str, data: Dict[str, Any]) -> Dict[str, Any]:
    cols = set(get_columns(cur, table))
    return {k: v for k, v in data.items() if k in cols}


def insert_dynamic(cur, table: str, data: Dict[str, Any]) -> bool:
    if not table_exists(cur, table):
        return False
    filtered = filter_by_columns(cur, table, data)
    if not filtered:
        return False
    keys = list(filtered.keys())
    col_sql = ", ".join(f"`{k}`" for k in keys)
    placeholders = ", ".join(["%s"] * len(keys))
    cur.execute(f"INSERT INTO `{table}` ({col_sql}) VALUES ({placeholders})", tuple(filtered[k] for k in keys))
    return True


def select_first_by_any_column(cur, table: str, values: Sequence[Any], candidate_cols: Sequence[str]) -> Optional[Dict[str, Any]]:
    cols = get_columns(cur, table)
    for col in candidate_cols:
        if col not in cols:
            continue
        for value in values:
            if value is None or value == "":
                continue
            cur.execute(f"SELECT * FROM `{table}` WHERE `{col}`=%s LIMIT 1", (value,))
            row = fetchone(cur)
            if row:
                return row
    return None


# ---------------------------------------------------------------------------
# Time / parsing helpers
# ---------------------------------------------------------------------------


def now_utc() -> dt.datetime:
    # Store UTC as a naive datetime because the MySQL schema uses DATETIME/TIMESTAMP.
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)


def unix_now() -> int:
    return int(dt.datetime.now(dt.timezone.utc).timestamp())


def audit_timestamp_value(cur) -> Any:
    types = get_column_types(cur, "audit_logs")
    t = types.get("timestamp", "")
    if any(x in t for x in ["int", "bigint", "decimal", "double", "float"]):
        return unix_now()
    return now_utc()


def parse_datetime_or_none(value: Any) -> Optional[dt.datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min)
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value, dt.timezone.utc).replace(tzinfo=None)
    if isinstance(value, str):
        raw = value.strip()
        for fmt in [None, "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"]:
            try:
                if fmt is None:
                    return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
                return dt.datetime.strptime(raw, fmt)
            except Exception:
                pass
    return None


def as_int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except Exception:
        raise ApiError(400, "INVALID_INTEGER", f"{field_name} must be an integer.")


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def require_fields(payload: Dict[str, Any], fields: Iterable[str]) -> None:
    missing = [field for field in fields if payload.get(field) in (None, "")]
    if missing:
        raise ApiError(400, "MISSING_FIELD", "Missing required fields.", missing)


def parse_jsonish(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return fallback
    return fallback


# ---------------------------------------------------------------------------
# User / role verification
# ---------------------------------------------------------------------------


def normalize_auth_type(raw: Any) -> str:
    auth_type = str(raw or "user").strip().lower()
    if auth_type in {"user", "admin", "member", "normal"}:
        return "user"
    if auth_type in {"guest", "guest_token", "visitor"}:
        return "guest_token"
    raise ApiError(400, "INVALID_AUTH_TYPE", "auth_type must be user for UC4.3 dashboard.")


def normalize_role(role: Any) -> str:
    return str(role or "").strip().lower()


def get_user(cur, user_id: str) -> Optional[Dict[str, Any]]:
    if not table_exists(cur, "users"):
        raise ApiError(500, "TABLE_MISSING", "users table does not exist.")
    return select_first_by_any_column(cur, "users", [user_id], ["user_id", "uid", "id", "username", "email"])


def user_identifiers(user: Dict[str, Any], original_user_id: str) -> List[Any]:
    values: List[Any] = [original_user_id]
    for key in ["id", "user_id", "uid", "u_id", "username", "email"]:
        if key in user and user[key] not in values:
            values.append(user[key])
    return values


def find_user_role_in_family(cur, user: Dict[str, Any], original_user_id: str, family_id: int) -> Optional[str]:
    identifiers = user_identifiers(user, original_user_id)
    tables = ["user_families", "family_members", "family_users", "members"]
    user_cols = ["user_id", "uid", "u_id", "member_id", "account_id"]
    family_cols = ["family_id", "home_id", "house_id", "field_id"]
    role_cols = ["role", "member_role", "family_role"]

    for table in tables:
        cols = get_columns(cur, table)
        if not cols:
            continue
        ucol = next((col for col in user_cols if col in cols), None)
        fcol = next((col for col in family_cols if col in cols), None)
        rcol = next((col for col in role_cols if col in cols), None)
        if not (ucol and fcol and rcol):
            continue

        status_clause = ""
        if "status" in cols:
            status_clause = " AND (`status` IS NULL OR LOWER(`status`) IN ('active','accepted','enabled','normal'))"
        for identifier in identifiers:
            cur.execute(
                f"SELECT `{rcol}` AS role FROM `{table}` WHERE `{ucol}`=%s AND `{fcol}`=%s{status_clause} LIMIT 1",
                (identifier, family_id),
            )
            row = fetchone(cur)
            if row and row.get("role"):
                return str(row["role"])

    for key in ["role", "user_role"]:
        if user.get(key):
            return str(user[key])
    return None


def verify_dashboard_viewer(cur, payload: Dict[str, Any], family_id: int) -> Dict[str, Any]:
    auth_type = normalize_auth_type(payload.get("auth_type", "user"))
    if auth_type != "user":
        raise ApiError(403, "GUEST_DASHBOARD_DENIED", "Guest tokens cannot query the family dashboard.")

    user_id = str(payload.get("user_id", "")).strip()
    if not user_id:
        raise ApiError(400, "MISSING_USER_ID", "user_id is required for UC4.3 dashboard.")

    user = get_user(cur, user_id)
    if not user:
        raise ApiError(401, "USER_NOT_FOUND", "User not found.")

    role_raw = find_user_role_in_family(cur, user, user_id, family_id)
    role = normalize_role(role_raw)
    if not role:
        raise ApiError(403, "ROLE_NOT_FOUND", "User has no role in this family.")
    if role not in {"admin", "owner", "member"}:
        raise ApiError(403, "ROLE_DENIED", "Only Admin/Member can view UC4.3 dashboard.")

    actor_id = str(user.get("user_id") or user.get("uid") or user.get("id") or user_id)
    return {
        "actor_id": actor_id,
        "user_id": actor_id,
        "u_id": user.get("id"),
        "role": str(role_raw or role),
        "user": user,
    }


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------


def build_audit_id(prefix: str = "DASH") -> str:
    return f"{prefix}_{now_utc().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}"


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
        if row and row.get("h"):
            return str(row["h"])
    except Exception:
        pass
    return "0" * 64


def write_dashboard_audit(
    cur,
    *,
    audit_id: str,
    family_id: int,
    user_id: Optional[str],
    u_id: Any,
    status: str,
    decision: str,
    reason: str,
    payload: Dict[str, Any],
    result_summary: Optional[Dict[str, Any]] = None,
) -> None:
    if not table_exists(cur, "audit_logs"):
        return
    prev_hash = get_prev_hash(cur)
    event_time = now_utc()
    raw_data = {
        "request": {
            "auth_type": payload.get("auth_type", "user"),
            "user_id": payload.get("user_id"),
            "family_id": payload.get("family_id"),
            "include_history": payload.get("include_history", False),
            "history_limit": payload.get("history_limit"),
        },
        "result_summary": result_summary or {},
    }
    raw_text = to_json_text(raw_data)
    actor_id = user_id or str(payload.get("user_id") or "unknown")
    hash_base = f"{prev_hash}|{event_time.isoformat()}|{audit_id}|{family_id}|{actor_id}|USER|DASHBOARD_VIEWED|{status}|{decision}|{reason}|{raw_text}"
    current_hash = hashlib.sha256(hash_base.encode("utf-8")).hexdigest()
    insert_dynamic(cur, "audit_logs", {
        "command_id": audit_id,
        "user_id": user_id,
        "u_id": u_id,
        "actor_id": actor_id,
        "actor_type": "USER",
        "device_id": None,
        "family_id": family_id,
        "action": "DASHBOARD_VIEWED",
        "parameters": to_json_text({"include_history": payload.get("include_history", False), "history_limit": payload.get("history_limit")}),
        "raw_data": raw_text,
        "status": status,
        "decision": decision,
        "reason": reason,
        "prev_hash": prev_hash,
        "current_hash": current_hash,
        "hash": current_hash,
        "timestamp": audit_timestamp_value(cur),
    })


# ---------------------------------------------------------------------------
# Dashboard query helpers
# ---------------------------------------------------------------------------


def get_family(cur, family_id: int) -> Optional[Dict[str, Any]]:
    cols = get_columns(cur, "families")
    if not cols:
        return None
    id_col = "id" if "id" in cols else ("family_id" if "family_id" in cols else None)
    if not id_col:
        return None
    cur.execute(f"SELECT * FROM `families` WHERE `{id_col}`=%s LIMIT 1", (family_id,))
    return fetchone(cur)


def get_family_devices(cur, family_id: int) -> List[Dict[str, Any]]:
    cols = get_columns(cur, "devices")
    if not cols:
        raise ApiError(500, "TABLE_MISSING", "devices table does not exist.")
    family_col = next((c for c in ["family_id", "home_id", "house_id", "field_id"] if c in cols), None)
    if not family_col:
        raise ApiError(500, "DEVICE_SCHEMA_INVALID", "devices table must contain family_id or compatible field.")
    order_col = "device_name" if "device_name" in cols else ("device_id" if "device_id" in cols else family_col)
    cur.execute(f"SELECT * FROM `devices` WHERE `{family_col}`=%s ORDER BY `{order_col}`", (family_id,))
    return fetchall(cur)


def get_device_id(device: Dict[str, Any]) -> str:
    for key in ["device_id", "id", "serial_number", "mac_address"]:
        if device.get(key) not in (None, ""):
            return str(device[key])
    return ""


def get_latest_telemetry(cur, family_id: int, device_id: str) -> Optional[Dict[str, Any]]:
    cols = get_columns(cur, "device_telemetry")
    if not cols:
        return None
    device_col = "device_id" if "device_id" in cols else None
    if not device_col:
        return None
    family_col = "family_id" if "family_id" in cols else None
    time_col = "recorded_at" if "recorded_at" in cols else ("timestamp" if "timestamp" in cols else ("log_id" if "log_id" in cols else device_col))

    if family_col:
        cur.execute(
            f"SELECT * FROM `device_telemetry` WHERE `{family_col}`=%s AND `{device_col}`=%s ORDER BY `{time_col}` DESC LIMIT 1",
            (family_id, device_id),
        )
        row = fetchone(cur)
        if row:
            return row

    cur.execute(f"SELECT * FROM `device_telemetry` WHERE `{device_col}`=%s ORDER BY `{time_col}` DESC LIMIT 1", (device_id,))
    return fetchone(cur)


def get_telemetry_history(cur, family_id: int, device_id: str, limit: int) -> List[Dict[str, Any]]:
    cols = get_columns(cur, "device_telemetry")
    if not cols:
        return []
    if "device_id" not in cols:
        return []
    family_col = "family_id" if "family_id" in cols else None
    time_col = "recorded_at" if "recorded_at" in cols else ("timestamp" if "timestamp" in cols else ("log_id" if "log_id" in cols else "device_id"))
    limit = max(1, min(int(limit), 20))
    if family_col:
        cur.execute(
            f"SELECT * FROM `device_telemetry` WHERE `{family_col}`=%s AND `device_id`=%s ORDER BY `{time_col}` DESC LIMIT %s",
            (family_id, device_id, limit),
        )
    else:
        cur.execute(
            f"SELECT * FROM `device_telemetry` WHERE `device_id`=%s ORDER BY `{time_col}` DESC LIMIT %s",
            (device_id, limit),
        )
    return fetchall(cur)


def get_latest_command(cur, family_id: int, device_id: str) -> Optional[Dict[str, Any]]:
    cols = get_columns(cur, "control_commands")
    if not cols:
        return None
    if "device_id" not in cols:
        return None
    family_col = "family_id" if "family_id" in cols else None
    time_col = "created_at" if "created_at" in cols else ("completed_at" if "completed_at" in cols else "command_id")
    selected_cols = [c for c in ["command_id", "actor_id", "actor_type", "action", "status", "reason", "created_at", "published_at", "completed_at"] if c in cols]
    select_sql = ", ".join(f"`{c}`" for c in selected_cols) or "*"
    if family_col:
        cur.execute(
            f"SELECT {select_sql} FROM `control_commands` WHERE `{family_col}`=%s AND `device_id`=%s ORDER BY `{time_col}` DESC LIMIT 1",
            (family_id, device_id),
        )
    else:
        cur.execute(f"SELECT {select_sql} FROM `control_commands` WHERE `device_id`=%s ORDER BY `{time_col}` DESC LIMIT 1", (device_id,))
    return fetchone(cur)


def telemetry_record_time(row: Optional[Dict[str, Any]]) -> Optional[dt.datetime]:
    if not row:
        return None
    for key in ["recorded_at", "timestamp", "created_at", "updated_at"]:
        parsed = parse_datetime_or_none(row.get(key))
        if parsed:
            return parsed
    return None


def int_or_none(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


def compute_connection_health(device: Dict[str, Any], telemetry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    offline_seconds = int(os.getenv("DASHBOARD_OFFLINE_SECONDS", "300"))
    battery_threshold = int(os.getenv("DASHBOARD_LOW_BATTERY", "20"))
    now = now_utc()

    device_status = str(device.get("status") or device.get("device_status") or "").strip().upper()
    telemetry_status = str((telemetry or {}).get("status") or "").strip().upper()
    battery = int_or_none((telemetry or {}).get("battery"))
    rssi = int_or_none((telemetry or {}).get("rssi"))
    recorded_at = telemetry_record_time(telemetry)

    low_battery = battery is not None and battery <= battery_threshold
    seconds_since_seen: Optional[int] = None
    if recorded_at:
        seconds_since_seen = max(0, int((now - recorded_at).total_seconds()))

    if device_status in {"FAULT", "FAILED", "ERROR", "BROKEN"} or telemetry_status in {"FAULT", "FAILED", "ERROR"}:
        health = "FAULT"
    elif not telemetry:
        health = "NO_DATA"
    elif recorded_at and seconds_since_seen is not None and seconds_since_seen > offline_seconds:
        health = "OFFLINE"
    elif rssi is None:
        health = "UNKNOWN"
    elif rssi >= -60:
        health = "GOOD"
    else:
        health = "WEAK"

    return {
        "connection_health": health,
        "low_battery": low_battery,
        "seconds_since_seen": seconds_since_seen,
    }


def build_history_row(row: Dict[str, Any]) -> Dict[str, Any]:
    telemetry_data = parse_jsonish(row.get("telemetry_data"), row.get("telemetry_data"))
    return {
        "log_id": row.get("log_id"),
        "command_id": row.get("command_id"),
        "status": row.get("status"),
        "physical_state": row.get("physical_state"),
        "battery": row.get("battery"),
        "rssi": row.get("rssi"),
        "recorded_at": row.get("recorded_at"),
        "telemetry_data": telemetry_data,
    }


def build_device_card(cur, family_id: int, device: Dict[str, Any], include_history: bool, history_limit: int) -> Dict[str, Any]:
    device_id = get_device_id(device)
    telemetry = get_latest_telemetry(cur, family_id, device_id) if device_id else None
    command = get_latest_command(cur, family_id, device_id) if device_id else None
    health = compute_connection_health(device, telemetry)
    telemetry_data = parse_jsonish((telemetry or {}).get("telemetry_data"), (telemetry or {}).get("telemetry_data"))

    card = {
        "device_id": device_id,
        "device_name": device.get("device_name") or device.get("name"),
        "device_type": device.get("device_type") or device.get("type"),
        "family_id": device.get("family_id") or family_id,
        "gateway_id": device.get("gateway_id"),
        "status": device.get("status"),
        "pairing_status": device.get("pairing_status"),
        "physical_state": (telemetry or {}).get("physical_state") or device.get("physical_state"),
        "battery": (telemetry or {}).get("battery"),
        "rssi": (telemetry or {}).get("rssi"),
        "connection_health": health["connection_health"],
        "low_battery": health["low_battery"],
        "last_seen_at": (telemetry or {}).get("recorded_at") or device.get("last_seen") or device.get("last_update"),
        "seconds_since_seen": health["seconds_since_seen"],
        "last_command_id": (telemetry or {}).get("command_id") or (command or {}).get("command_id"),
        "last_command": command,
        "telemetry_data": telemetry_data,
    }

    if include_history:
        card["history"] = [build_history_row(row) for row in get_telemetry_history(cur, family_id, device_id, history_limit)]
    return card


def build_summary(devices: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(devices)
    online = sum(1 for d in devices if d.get("connection_health") in {"GOOD", "WEAK", "UNKNOWN"})
    offline = sum(1 for d in devices if d.get("connection_health") in {"OFFLINE", "NO_DATA"})
    fault = sum(1 for d in devices if d.get("connection_health") == "FAULT")
    low_battery = sum(1 for d in devices if d.get("low_battery") is True)
    return {
        "total_devices": total,
        "online_devices": online,
        "offline_devices": offline,
        "fault_devices": fault,
        "low_battery_devices": low_battery,
    }


# ---------------------------------------------------------------------------
# Main UC4.3 pipeline
# ---------------------------------------------------------------------------


def handle_dashboard(payload: Dict[str, Any]) -> Dict[str, Any]:
    require_fields(payload, ["user_id", "family_id"])
    family_id = as_int(payload["family_id"], "family_id")
    include_history = as_bool(payload.get("include_history"), False)
    history_limit = as_int(payload.get("history_limit", 5), "history_limit")
    history_limit = max(1, min(history_limit, 20))
    audit_id = build_audit_id()

    conn = get_db_connection()
    cur = dict_cursor(conn)
    viewer: Optional[Dict[str, Any]] = None

    try:
        viewer = verify_dashboard_viewer(cur, payload, family_id)
        family = get_family(cur, family_id)
        if not family:
            raise ApiError(404, "FAMILY_NOT_FOUND", "Family not found.")

        device_rows = get_family_devices(cur, family_id)
        device_cards = [build_device_card(cur, family_id, device, include_history, history_limit) for device in device_rows]
        summary = build_summary(device_cards)

        write_dashboard_audit(
            cur,
            audit_id=audit_id,
            family_id=family_id,
            user_id=viewer["user_id"],
            u_id=viewer.get("u_id"),
            status="SUCCEEDED",
            decision="ALLOW",
            reason="DASHBOARD_LOADED",
            payload=payload,
            result_summary=summary,
        )
        conn.commit()

        return {
            "status": "Success",
            "message": "DASHBOARD_LOADED",
            "data": {
                "uc": "UC4.3",
                "audit_id": audit_id,
                "family_id": family_id,
                "family": {
                    "family_id": family.get("id") or family.get("family_id") or family_id,
                    "family_name": family.get("family_name") or family.get("name"),
                    "admin_uid": family.get("admin_uid"),
                },
                "viewer": {
                    "user_id": viewer["user_id"],
                    "role": viewer["role"],
                },
                "summary": summary,
                "devices": device_cards,
                "history": {
                    "included": include_history,
                    "history_limit": history_limit if include_history else 0,
                },
            },
        }

    except ApiError as exc:
        try:
            # Record denied UC4.3 access when possible. Do not let audit failure hide the real error.
            if table_exists(cur, "audit_logs"):
                write_dashboard_audit(
                    cur,
                    audit_id=audit_id,
                    family_id=family_id,
                    user_id=(viewer or {}).get("user_id") or str(payload.get("user_id") or ""),
                    u_id=(viewer or {}).get("u_id"),
                    status="DENIED",
                    decision="DENY",
                    reason=f"{exc.code}: {exc.message}",
                    payload=payload,
                    result_summary={"error": exc.code},
                )
                conn.commit()
        except Exception:
            conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def main() -> None:
    try:
        payload = read_payload()
        result = handle_dashboard(payload)
        respond(200, result)
    except ApiError as exc:
        respond(exc.status_code, {"status": "Error", "code": exc.code, "message": exc.message, "detail": exc.detail})
    except json.JSONDecodeError as exc:
        respond(400, {"status": "Error", "code": "INVALID_JSON", "message": "Request body must be valid JSON.", "detail": str(exc)})
    except Exception as exc:
        detail = traceback.format_exc() if os.getenv("DEBUG", "0") == "1" else str(exc)
        respond(500, {"status": "Error", "code": "INTERNAL_ERROR", "message": "Unexpected server error.", "detail": detail})


if __name__ == "__main__":
    main()
