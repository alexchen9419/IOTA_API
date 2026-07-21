#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC4.1 / UC4.2 control_device.py

UC4.1: Admin / Member sends a remote control command. Gateway validates identity,
       family/device ownership and zero-trust policy, then executes the command.
UC4.2: Guest uses a short-lived token. Gateway validates expiry, usage count,
       device scope and allowed action before executing the same control pipeline.

Default execution is mock mode, so the API can be tested before ESP32 is ready.
Set CONTROL_MODE=mqtt to use the real MQTT adapter.

CGI input examples are documented in README_UC4_1_4_2.md.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import sys
import traceback
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


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


def update_dynamic(cur, table: str, data: Dict[str, Any], where_sql: str, where_params: Sequence[Any]) -> int:
    if not table_exists(cur, table):
        return 0
    filtered = filter_by_columns(cur, table, data)
    if not filtered:
        return 0
    set_sql = ", ".join(f"`{k}`=%s" for k in filtered)
    cur.execute(f"UPDATE `{table}` SET {set_sql} WHERE {where_sql}", tuple(filtered.values()) + tuple(where_params))
    return int(getattr(cur, "rowcount", 0) or 0)


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
    return dt.datetime.utcnow().replace(microsecond=0)


def unix_now() -> int:
    return int(dt.datetime.utcnow().timestamp())


def audit_timestamp_value(cur) -> Any:
    types = get_column_types(cur, "audit_logs")
    t = types.get("timestamp", "")
    if any(x in t for x in ["int", "bigint", "decimal", "double", "float"]):
        return unix_now()
    return now_utc()


def parse_datetime(value: Any, field_name: str) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min)
    if isinstance(value, (int, float)):
        return dt.datetime.utcfromtimestamp(value)
    if isinstance(value, str):
        raw = value.strip()
        for fmt in [None, "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"]:
            try:
                if fmt is None:
                    return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
                return dt.datetime.strptime(raw, fmt)
            except Exception:
                pass
    raise ApiError(500, "INVALID_DATETIME", f"{field_name} format is invalid.")


def as_int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except Exception:
        raise ApiError(400, "INVALID_INTEGER", f"{field_name} must be an integer.")


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
# Domain model helpers
# ---------------------------------------------------------------------------


ALLOWED_ACTIONS = {
    "LOCK",
    "UNLOCK",
    "ON",
    "OFF",
    "OPEN",
    "CLOSE",
    "TOGGLE",
    "START",
    "STOP",
}

STATE_AFTER_ACTION = {
    "LOCK": "LOCKED",
    "UNLOCK": "UNLOCKED",
    "ON": "ON",
    "OFF": "OFF",
    "OPEN": "OPEN",
    "CLOSE": "CLOSED",
    "TOGGLE": "TOGGLED",
    "START": "RUNNING",
    "STOP": "STOPPED",
}


def normalize_action(action: Any) -> str:
    if not isinstance(action, str) or not action.strip():
        raise ApiError(400, "INVALID_ACTION", "action is required.")
    action = action.strip().upper()
    if action not in ALLOWED_ACTIONS:
        raise ApiError(400, "UNSUPPORTED_ACTION", f"Unsupported action: {action}")
    return action


def normalize_auth_type(raw: Any) -> str:
    auth_type = str(raw or "user").strip().lower()
    if auth_type in {"user", "admin", "member", "normal"}:
        return "user"
    if auth_type in {"guest", "guest_token", "visitor"}:
        return "guest_token"
    raise ApiError(400, "INVALID_AUTH_TYPE", "auth_type must be user or guest_token.")


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


def get_device(cur, device_id: str) -> Dict[str, Any]:
    if not table_exists(cur, "devices"):
        raise ApiError(500, "TABLE_MISSING", "devices table does not exist.")
    row = select_first_by_any_column(cur, "devices", [device_id], ["device_id", "id", "serial_number", "mac_address"])
    if not row:
        raise ApiError(404, "DEVICE_NOT_FOUND", "Device not found.")
    return row


def device_family_id(device: Dict[str, Any]) -> Optional[int]:
    for key in ["family_id", "home_id", "house_id", "field_id"]:
        if device.get(key) not in (None, ""):
            return int(device[key])
    return None


def device_is_active(device: Dict[str, Any]) -> bool:
    for key in ["status", "device_status", "state"]:
        if key in device and device[key] not in (None, ""):
            value = str(device[key]).strip().lower()
            if value in {"decommissioned", "disabled", "revoked", "deleted", "removed", "inactive", "blocked", "0"}:
                return False
            if value in {"active", "paired", "online", "enabled", "normal", "1"}:
                return True
    # Some older schemas do not have a device status column. Do not block MVP execution.
    return True


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

    # Fallback for early MVP DBs: users.role as global role.
    for key in ["role", "user_role"]:
        if user.get(key):
            return str(user[key])
    return None


# ---------------------------------------------------------------------------
# Zero-trust policy bridge
# ---------------------------------------------------------------------------


def evaluate_policy(cur, family_id: int, device_id: str, role: str, action: str) -> Tuple[bool, str]:
    """Evaluate policy_rules if present.

    If no compatible policy table exists, basic Admin/Member/Guest checks remain
    the effective policy for MVP execution.
    """
    cols = get_columns(cur, "policy_rules")
    if not cols:
        return True, "ALLOW_NO_POLICY_TABLE"

    family_col = next((c for c in ["family_id", "home_id", "house_id", "field_id"] if c in cols), None)
    effect_col = next((c for c in ["effect", "decision", "permission", "allow_deny"] if c in cols), None)
    if not family_col or not effect_col:
        return True, "ALLOW_POLICY_TABLE_INCOMPATIBLE"

    device_col = next((c for c in ["device_id", "target_device_id"] if c in cols), None)
    role_col = next((c for c in ["role", "subject_role", "actor_role"] if c in cols), None)
    action_col = next((c for c in ["action", "allowed_action", "function_name", "operation"] if c in cols), None)
    enabled_col = next((c for c in ["enabled", "is_enabled"] if c in cols), None)

    where = [f"`{family_col}`=%s"]
    params: List[Any] = [family_id]
    if device_col:
        where.append(f"(`{device_col}`=%s OR `{device_col}`='*' OR `{device_col}` IS NULL)")
        params.append(device_id)
    if role_col:
        where.append(f"(LOWER(`{role_col}`)=%s OR `{role_col}`='*' OR `{role_col}` IS NULL)")
        params.append(role.lower())
    if action_col:
        where.append(f"(UPPER(`{action_col}`)=%s OR `{action_col}`='*' OR `{action_col}` IS NULL)")
        params.append(action.upper())
    if enabled_col:
        where.append(f"(`{enabled_col}`=1 OR `{enabled_col}` IS NULL)")

    order_col = "id" if "id" in cols else effect_col
    sql = f"SELECT * FROM policy_rules WHERE {' AND '.join(where)} ORDER BY `{order_col}` DESC LIMIT 20"
    try:
        cur.execute(sql, tuple(params))
        rows = fetchall(cur)
    except Exception as exc:
        return True, f"ALLOW_POLICY_QUERY_FAILED:{exc}"

    if not rows:
        return True, "ALLOW_NO_MATCHING_POLICY"

    for row in rows:
        effect = str(row.get(effect_col, "")).strip().lower()
        if effect in {"deny", "denied", "false", "0", "block"}:
            return False, "POLICY_DENIED"
    for row in rows:
        effect = str(row.get(effect_col, "")).strip().lower()
        if effect in {"allow", "allowed", "permit", "true", "1"}:
            return True, "ALLOW_BY_POLICY"
    return True, "ALLOW_POLICY_NO_DENY"


# ---------------------------------------------------------------------------
# Auth verification
# ---------------------------------------------------------------------------


def verify_user_control(cur, payload: Dict[str, Any], family_id: int, device_id: str, action: str) -> Tuple[str, str]:
    user_id = str(payload.get("user_id", "")).strip()
    if not user_id:
        raise ApiError(400, "MISSING_USER_ID", "user_id is required for auth_type=user.")

    user = get_user(cur, user_id)
    if not user:
        raise ApiError(401, "USER_NOT_FOUND", "User not found.")

    role = normalize_role(find_user_role_in_family(cur, user, user_id, family_id))
    if not role:
        raise ApiError(403, "ROLE_NOT_FOUND", "User has no role in this family.")
    if role not in {"admin", "owner", "member"}:
        raise ApiError(403, "ROLE_DENIED", "Only Admin/Member can execute UC4.1 remote control.")

    ok, reason = evaluate_policy(cur, family_id, device_id, role, action)
    if not ok:
        raise ApiError(403, "POLICY_DENIED", reason)

    # Keep actor_id stable as external user_id when available.
    actor_id = str(user.get("user_id") or user.get("uid") or user.get("id") or user_id)
    return actor_id, "USER"


def verify_guest_control(cur, payload: Dict[str, Any], family_id: int, device_id: str, action: str) -> Tuple[str, str, Dict[str, Any]]:
    token_plain = str(payload.get("guest_token", "")).strip()
    if not token_plain:
        raise ApiError(400, "MISSING_GUEST_TOKEN", "guest_token is required for auth_type=guest_token.")
    if not table_exists(cur, "guest_tokens"):
        raise ApiError(500, "TABLE_MISSING", "guest_tokens table does not exist. Apply uc4_1_4_2_final.sql first.")

    token_hash = hashlib.sha256(token_plain.encode("utf-8")).hexdigest()
    cols = get_columns(cur, "guest_tokens")
    where_parts: List[str] = []
    params: List[Any] = []
    if "token_hash" in cols:
        where_parts.append("`token_hash`=%s")
        params.append(token_hash)
    # Compatibility with any existing UC3.4 table that stores token_id as plaintext.
    if "token_id" in cols:
        where_parts.append("`token_id`=%s")
        params.append(token_plain)
    if not where_parts:
        raise ApiError(500, "TOKEN_SCHEMA_INVALID", "guest_tokens must contain token_hash or token_id.")

    cur.execute(f"SELECT * FROM guest_tokens WHERE {' OR '.join(where_parts)} LIMIT 1 FOR UPDATE", tuple(params))
    token = fetchone(cur)
    if not token:
        raise ApiError(403, "TOKEN_NOT_FOUND", "Guest token not found.")

    if str(token.get("revoked", 0)).lower() in {"1", "true", "yes", "revoked"}:
        raise ApiError(403, "TOKEN_REVOKED", "Guest token has been revoked.")

    token_family = token.get("family_id")
    if token_family is not None and int(token_family) != int(family_id):
        raise ApiError(403, "TOKEN_FAMILY_MISMATCH", "Guest token cannot access this family.")

    token_device = token.get("device_id")
    if token_device not in (None, "", "*") and str(token_device) != str(device_id):
        raise ApiError(403, "TOKEN_DEVICE_MISMATCH", "Guest token cannot access this device.")

    expires_at = token.get("expires_at")
    if expires_at is None:
        raise ApiError(500, "TOKEN_SCHEMA_INVALID", "guest_tokens.expires_at is required.")
    if parse_datetime(expires_at, "expires_at") <= now_utc():
        raise ApiError(403, "TOKEN_EXPIRED", "Guest token has expired.")

    used_count = int(token.get("used_count", 0) or 0)
    max_uses = int(token.get("max_uses", 1) or 1)
    if used_count >= max_uses:
        raise ApiError(403, "TOKEN_USED_UP", "Guest token usage count has been exhausted.")

    allowed_raw = token.get("allowed_actions")
    allowed = parse_jsonish(allowed_raw, [])
    if isinstance(allowed, str):
        allowed = [allowed]
    allowed_set = {str(a).strip().upper() for a in allowed if str(a).strip()}
    if allowed_set and "*" not in allowed_set and action.upper() not in allowed_set:
        raise ApiError(403, "TOKEN_ACTION_DENIED", "Guest token cannot execute this action.")

    token_id = str(token.get("token_id") or token_hash[:16])
    return token_id, "GUEST", token


def consume_guest_token(cur, token: Dict[str, Any]) -> None:
    cols = get_columns(cur, "guest_tokens")
    key_col = "token_id" if "token_id" in cols else None
    if not key_col or token.get(key_col) is None:
        return
    used = int(token.get("used_count", 0) or 0) + 1
    update_dynamic(
        cur,
        "guest_tokens",
        {"used_count": used, "last_used_at": now_utc()},
        f"`{key_col}`=%s",
        (token.get(key_col),),
    )


# ---------------------------------------------------------------------------
# Control adapters
# ---------------------------------------------------------------------------


class ControlResult:
    def __init__(self, ok: bool, command_status: str, reason: str, topic: Optional[str] = None, response_payload: Optional[Dict[str, Any]] = None):
        self.ok = ok
        self.command_status = command_status
        self.reason = reason
        self.topic = topic
        self.response_payload = response_payload or {}


class ControlAdapter:
    mode = "base"

    def execute(self, command_payload: Dict[str, Any]) -> ControlResult:
        raise NotImplementedError


class MockControlAdapter(ControlAdapter):
    mode = "mock"

    def execute(self, command_payload: Dict[str, Any]) -> ControlResult:
        action = str(command_payload.get("action", "")).upper()
        physical_state = STATE_AFTER_ACTION.get(action, action)
        response = {
            "command_id": command_payload.get("command_id"),
            "family_id": command_payload.get("family_id"),
            "device_id": command_payload.get("device_id"),
            "status": "SUCCEEDED",
            "physical_state": physical_state,
            "battery": int(os.getenv("MOCK_BATTERY", "87")),
            "rssi": int(os.getenv("MOCK_RSSI", "-52")),
            "mode": "mock",
            "executed_at": now_utc().isoformat(sep=" "),
        }
        return ControlResult(True, "SUCCEEDED", "MOCK_CONTROL_SUCCEEDED", None, response)


class MqttControlAdapter(ControlAdapter):
    mode = "mqtt"

    def execute(self, command_payload: Dict[str, Any]) -> ControlResult:
        family_id = command_payload["family_id"]
        device_id = command_payload["device_id"]
        topic = f"home/{family_id}/device/{device_id}/cmd"
        payload_text = to_json_text(command_payload)

        try:
            import paho.mqtt.client as mqtt  # type: ignore
        except ImportError:
            return ControlResult(False, "FAILED", "MQTT_DRIVER_MISSING: install paho-mqtt", topic, {})

        host = os.getenv("MQTT_HOST", "localhost")
        port = int(os.getenv("MQTT_PORT", "1883"))
        username = os.getenv("MQTT_USERNAME")
        password = os.getenv("MQTT_PASSWORD")
        use_tls = os.getenv("MQTT_USE_TLS", "0") == "1"
        qos = int(os.getenv("MQTT_QOS", "1"))
        timeout = float(os.getenv("MQTT_TIMEOUT", "5"))

        client = mqtt.Client(client_id=f"gateway-uc4-{secrets.token_hex(4)}")
        if username:
            client.username_pw_set(username, password=password)
        if use_tls:
            client.tls_set()

        try:
            client.connect(host, port, keepalive=15)
            client.loop_start()
            result = client.publish(topic, payload=payload_text, qos=qos, retain=False)
            result.wait_for_publish(timeout=timeout)
            client.loop_stop()
            client.disconnect()
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                return ControlResult(False, "FAILED", f"MQTT_PUBLISH_FAILED_RC_{result.rc}", topic, {})
            # Real device result is expected later through device_status_update.py or MQTT worker.
            return ControlResult(True, "PUBLISHED", "MQTT_PUBLISHED_WAITING_FOR_DEVICE_ACK", topic, {})
        except Exception as exc:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:
                pass
            return ControlResult(False, "FAILED", f"MQTT_ERROR:{exc}", topic, {})


def get_control_adapter() -> ControlAdapter:
    mode = os.getenv("CONTROL_MODE", os.getenv("MQTT_MOCK", "mock")).strip().lower()
    if mode in {"1", "true", "yes", "mock"}:
        return MockControlAdapter()
    if mode in {"mqtt", "real", "esp32"}:
        return MqttControlAdapter()
    raise ApiError(500, "INVALID_CONTROL_MODE", "CONTROL_MODE must be mock or mqtt.")


# ---------------------------------------------------------------------------
# Command / telemetry / audit persistence
# ---------------------------------------------------------------------------


def build_command_id() -> str:
    return f"CMD_{now_utc().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}"


def build_command_payload(command_id: str, family_id: int, device_id: str, action: str, parameters: Dict[str, Any], actor_id: str, actor_type: str) -> Dict[str, Any]:
    return {
        "command_id": command_id,
        "family_id": family_id,
        "device_id": device_id,
        "action": action,
        "parameters": parameters or {},
        "actor_id": actor_id,
        "actor_type": actor_type,
        "timestamp": now_utc().isoformat(sep=" "),
        "nonce": secrets.token_hex(8),
    }


def record_command(cur, *, command_id: str, family_id: int, device_id: str, actor_id: str, actor_type: str, action: str, parameters: Dict[str, Any], control_mode: str, status: str, reason: str, request_payload: Dict[str, Any], target_topic: Optional[str] = None, response_payload: Optional[Dict[str, Any]] = None) -> None:
    now = now_utc()
    insert_dynamic(cur, "control_commands", {
        "command_id": command_id,
        "family_id": family_id,
        "device_id": device_id,
        "actor_id": actor_id,
        "actor_type": actor_type,
        "action": action,
        "parameters": to_json_text(parameters or {}),
        "control_mode": control_mode,
        "target_topic": target_topic,
        "request_payload": to_json_text(request_payload),
        "response_payload": to_json_text(response_payload or {}),
        "status": status,
        "reason": reason,
        "created_at": now,
        "published_at": now if status in {"PUBLISHED", "SUCCEEDED"} else None,
        "completed_at": now if status in {"SUCCEEDED", "FAILED", "DENIED"} else None,
    })


def update_command(cur, command_id: str, status: str, reason: str, response_payload: Optional[Dict[str, Any]] = None) -> None:
    now = now_utc()
    data = {
        "status": status,
        "reason": reason,
        "response_payload": to_json_text(response_payload or {}),
        "published_at": now if status == "PUBLISHED" else None,
        "completed_at": now if status in {"SUCCEEDED", "FAILED", "DENIED", "TIMEOUT"} else None,
    }
    # Avoid setting published_at to NULL on later updates.
    if status != "PUBLISHED":
        data.pop("published_at", None)
    update_dynamic(cur, "control_commands", data, "`command_id`=%s", (command_id,))


def update_device_shadow(cur, family_id: int, device_id: str, physical_state: Optional[str], response_payload: Dict[str, Any]) -> None:
    now = now_utc()
    device_cols = get_columns(cur, "devices")
    if device_cols:
        device_key = "device_id" if "device_id" in device_cols else ("id" if "id" in device_cols else None)
        if device_key:
            update_dynamic(cur, "devices", {
                "physical_state": physical_state,
                "online_status": "Online",
                "last_command": response_payload.get("action"),
                "last_seen": now,
                "last_update": now,
                "updated_at": now,
                "battery": response_payload.get("battery"),
                "rssi": response_payload.get("rssi"),
            }, f"`{device_key}`=%s", (device_id,))

    # Insert a telemetry row for dashboard UC4.3 and later UC5.3 traceability.
    insert_dynamic(cur, "device_telemetry", {
        "family_id": family_id,
        "device_id": device_id,
        "command_id": response_payload.get("command_id"),
        "physical_state": physical_state,
        "status": response_payload.get("status", "SUCCEEDED"),
        "telemetry_data": to_json_text(response_payload),
        "battery": response_payload.get("battery"),
        "rssi": response_payload.get("rssi"),
        "raw_data": to_json_text(response_payload),
        "recorded_at": now,
        "created_at": now,
    })


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


def write_audit(cur, *, command_id: str, family_id: int, device_id: str, actor_id: str, actor_type: str, action: str, status: str, decision: str, reason: str, parameters: Dict[str, Any], raw_data: Dict[str, Any]) -> None:
    if not table_exists(cur, "audit_logs"):
        return
    prev_hash = get_prev_hash(cur)
    created_at = now_utc()
    raw_text = to_json_text(raw_data)
    hash_base = f"{prev_hash}|{created_at.isoformat()}|{command_id}|{family_id}|{device_id}|{actor_id}|{actor_type}|{action}|{status}|{decision}|{reason}|{raw_text}"
    current_hash = hashlib.sha256(hash_base.encode("utf-8")).hexdigest()
    insert_dynamic(cur, "audit_logs", {
        "command_id": command_id,
        "user_id": actor_id,
        "actor_id": actor_id,
        "actor_type": actor_type,
        "device_id": device_id,
        "family_id": family_id,
        "action": action,
        "parameters": to_json_text(parameters or {}),
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


# ---------------------------------------------------------------------------
# Main control pipeline
# ---------------------------------------------------------------------------


def validate_device_scope(cur, family_id: int, device_id: str) -> Dict[str, Any]:
    device = get_device(cur, device_id)
    dfid = device_family_id(device)
    if dfid is not None and int(dfid) != int(family_id):
        raise ApiError(403, "DEVICE_FAMILY_MISMATCH", "Device does not belong to this family.")
    if not device_is_active(device):
        raise ApiError(409, "DEVICE_NOT_ACTIVE", "Device is not active, paired, or enabled.")
    return device


def handle_control(payload: Dict[str, Any]) -> Dict[str, Any]:
    require_fields(payload, ["family_id", "device_id", "action"])
    family_id = as_int(payload["family_id"], "family_id")
    device_id = str(payload["device_id"]).strip()
    action = normalize_action(payload["action"])
    parameters = payload.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise ApiError(400, "INVALID_PARAMETERS", "parameters must be an object.")
    auth_type = normalize_auth_type(payload.get("auth_type", "user"))
    command_id = str(payload.get("command_id") or build_command_id())

    conn = get_db_connection()
    cur = dict_cursor(conn)
    actor_id = str(payload.get("user_id") or "unknown")[:128]
    actor_type = "USER" if auth_type == "user" else "GUEST"
    adapter = get_control_adapter()

    try:
        # Validate device first so cross-family attempts are blocked before auth-specific control.
        validate_device_scope(cur, family_id, device_id)

        guest_token_row: Optional[Dict[str, Any]] = None
        if auth_type == "user":
            actor_id, actor_type = verify_user_control(cur, payload, family_id, device_id, action)
        else:
            actor_id, actor_type, guest_token_row = verify_guest_control(cur, payload, family_id, device_id, action)

        command_payload = build_command_payload(command_id, family_id, device_id, action, parameters, actor_id, actor_type)
        record_command(
            cur,
            command_id=command_id,
            family_id=family_id,
            device_id=device_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action=action,
            parameters=parameters,
            control_mode=adapter.mode,
            status="ACCEPTED",
            reason="VALIDATION_PASSED",
            request_payload=command_payload,
        )

        result = adapter.execute(command_payload)
        if result.ok and guest_token_row is not None:
            consume_guest_token(cur, guest_token_row)

        update_command(cur, command_id, result.command_status, result.reason, result.response_payload)

        if adapter.mode == "mock" and result.ok:
            physical_state = result.response_payload.get("physical_state")
            update_device_shadow(cur, family_id, device_id, physical_state, result.response_payload)

        if result.ok:
            write_audit(
                cur,
                command_id=command_id,
                family_id=family_id,
                device_id=device_id,
                actor_id=actor_id,
                actor_type=actor_type,
                action="CONTROL_DEVICE",
                status=result.command_status,
                decision="ALLOW",
                reason=result.reason,
                parameters=parameters,
                raw_data={"request": payload, "command_payload": command_payload, "response_payload": result.response_payload, "target_topic": result.topic},
            )
            conn.commit()
            data: Dict[str, Any] = {
                "uc": "UC4.1" if auth_type == "user" else "UC4.2",
                "command_id": command_id,
                "family_id": family_id,
                "device_id": device_id,
                "action": action,
                "control_mode": adapter.mode,
                "command_status": result.command_status,
                "target_topic": result.topic,
                "response_payload": result.response_payload,
            }
            if guest_token_row is not None:
                data["guest_token_usage"] = {
                    "used_count_before": int(guest_token_row.get("used_count", 0) or 0),
                    "used_count_after": int(guest_token_row.get("used_count", 0) or 0) + 1,
                    "max_uses": int(guest_token_row.get("max_uses", 1) or 1),
                }
            return {"status": "Success", "message": result.reason, "data": data}

        write_audit(
            cur,
            command_id=command_id,
            family_id=family_id,
            device_id=device_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action="CONTROL_DEVICE",
            status="FAILED",
            decision="DENY",
            reason=result.reason,
            parameters=parameters,
            raw_data={"request": payload, "target_topic": result.topic},
        )
        conn.commit()
        raise ApiError(502, "CONTROL_EXECUTION_FAILED", "Gateway failed to execute the control command.", result.reason)

    except ApiError as exc:
        try:
            # Record rejected requests in both command history and audit_logs when possible.
            record_command(
                cur,
                command_id=command_id,
                family_id=family_id,
                device_id=device_id,
                actor_id=actor_id,
                actor_type=actor_type,
                action=action,
                parameters=parameters,
                control_mode=adapter.mode,
                status="DENIED",
                reason=f"{exc.code}: {exc.message}",
                request_payload={"request": payload},
                response_payload={"error": exc.code, "message": exc.message, "detail": exc.detail},
            )
            write_audit(
                cur,
                command_id=command_id,
                family_id=family_id,
                device_id=device_id,
                actor_id=actor_id,
                actor_type=actor_type,
                action="CONTROL_DEVICE",
                status="DENIED",
                decision="DENY",
                reason=f"{exc.code}: {exc.message}",
                parameters=parameters,
                raw_data={"request": payload, "detail": exc.detail},
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
        result = handle_control(payload)
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
