#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Common ledger-event queue helpers.

Business APIs call :func:`enqueue_ledger_event` inside the same MySQL
transaction as their business update and audit log.  This module **does not**
submit anything to IOTA.  A separate Ledger Worker is expected to consume
rows whose status is ``PENDING``.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from typing import Any, Dict, Optional

LEDGER_SCHEMA_VERSION = "1.0"


def json_default(obj: Any) -> str:
    if isinstance(obj, (dt.datetime, dt.date)):
        return obj.isoformat()
    return str(obj)


def canonical_json(value: Any) -> str:
    """Return the canonical JSON representation used by this repository.

    The project specification currently defines canonical JSON as UTF-8 JSON
    with sorted keys and compact separators.  All API producers use this same
    helper so Python-generated ``payload_hash`` values are deterministic.
    """
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=json_default,
    )


def sha256_hex(value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def hash_identifier(value: Any) -> Optional[str]:
    """Hash an identifier for ledger payloads without exposing the raw value."""
    if value in (None, ""):
        return None
    return f"sha256:{sha256_hex(str(value))}"


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_utc(value: Optional[dt.datetime] = None) -> str:
    value = value or utc_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _fetchone(cursor) -> Optional[Dict[str, Any]]:
    row = cursor.fetchone()
    return row if isinstance(row, dict) else None


def _table_columns(cursor, table: str) -> set[str]:
    cursor.execute(f"SHOW COLUMNS FROM `{table}`")
    rows = cursor.fetchall() or []
    return {str(row.get("Field")) for row in rows if isinstance(row, dict) and row.get("Field")}


def get_ledger_event_by_dedup(cursor, dedup_key: str, *, for_update: bool = False) -> Optional[Dict[str, Any]]:
    sql = """
        SELECT event_id, dedup_key, uc_id, event_type, family_id, gateway_id,
               device_id, created_by, payload, payload_hash, status, ledger_reference,
               retry_count, last_error, created_at, submitted_at, confirmed_at,
               updated_at
        FROM ledger_events
        WHERE dedup_key = %s
        LIMIT 1
    """
    if for_update:
        sql += " FOR UPDATE"
    cursor.execute(sql, (dedup_key,))
    return _fetchone(cursor)


def _event_result(row: Dict[str, Any], *, created: bool) -> Dict[str, Any]:
    return {
        "event_id": row.get("event_id"),
        "dedup_key": row.get("dedup_key"),
        "uc_id": row.get("uc_id"),
        "event_type": row.get("event_type"),
        "status": row.get("status") or "PENDING",
        "payload_hash": row.get("payload_hash"),
        "ledger_reference": row.get("ledger_reference"),
        "created": created,
    }


def enqueue_ledger_event(
    cursor,
    *,
    uc_id: str,
    event_type: str,
    dedup_key: str,
    family_id: Optional[int],
    gateway_id: Optional[str],
    created_by: Optional[str],
    source: str,
    actor: Dict[str, Any],
    payload: Dict[str, Any],
    device_id: Optional[str] = None,
    timestamp: Optional[str] = None,
    event_id: Optional[str] = None,
    schema_version: str = LEDGER_SCHEMA_VERSION,
    extra_top_level: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Insert one idempotent PENDING event and return queue metadata.

    ``dedup_key`` defines business idempotency.  If it already exists, the
    existing event is returned instead of creating a second row.
    """
    existing = get_ledger_event_by_dedup(cursor, dedup_key, for_update=True)
    if existing:
        return _event_result(existing, created=False)

    event_id = str(event_id or uuid.uuid4())
    event: Dict[str, Any] = {
        "schema_version": str(schema_version),
        "uc_id": str(uc_id),
        "event_id": event_id,
        "event_type": str(event_type),
        "family_id": int(family_id) if family_id is not None else None,
        "gateway_id": str(gateway_id) if gateway_id not in (None, "") else None,
    }
    if device_id not in (None, ""):
        event["device_id"] = str(device_id)
    event["source"] = str(source)
    event["timestamp"] = str(timestamp or iso_utc())
    event["actor"] = actor
    event["payload"] = payload
    if extra_top_level:
        # Reserved fields cannot be replaced by caller-supplied metadata.
        reserved = {
            "schema_version", "uc_id", "event_id", "event_type", "family_id",
            "gateway_id", "device_id", "source", "timestamp", "actor", "payload",
        }
        for key, value in extra_top_level.items():
            if key not in reserved:
                event[key] = value

    payload_json = canonical_json(event)
    payload_hash = sha256_hex(payload_json)

    columns = _table_columns(cursor, "ledger_events")
    insert_data: Dict[str, Any] = {
        "event_id": event_id,
        "dedup_key": dedup_key,
        "uc_id": uc_id,
        "event_type": event_type,
        "family_id": family_id,
        "gateway_id": gateway_id,
        "device_id": device_id,
        "created_by": created_by,
        "payload": payload_json,
        "payload_hash": payload_hash,
        "status": "PENDING",
        "retry_count": 0,
    }
    insert_data = {key: value for key, value in insert_data.items() if key in columns}
    if "payload" not in insert_data or "payload_hash" not in insert_data:
        raise RuntimeError("ledger_events schema is missing required payload/payload_hash columns")

    keys = list(insert_data.keys())
    cursor.execute(
        f"INSERT INTO ledger_events ({', '.join(f'`{k}`' for k in keys)}) "
        f"VALUES ({', '.join(['%s'] * len(keys))})",
        tuple(insert_data[key] for key in keys),
    )

    return {
        "event_id": event_id,
        "dedup_key": dedup_key,
        "uc_id": uc_id,
        "event_type": event_type,
        "status": "PENDING",
        "payload_hash": payload_hash,
        "ledger_reference": None,
        "created": True,
    }


def resolve_gateway_id(
    cursor,
    *,
    family_id: Optional[int],
    preferred_gateway_id: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve the gateway attached to a device/family without inventing one."""
    if preferred_gateway_id not in (None, ""):
        return str(preferred_gateway_id)

    if device_id not in (None, ""):
        try:
            cursor.execute("SELECT gateway_id FROM devices WHERE device_id=%s LIMIT 1", (device_id,))
            row = _fetchone(cursor)
            if row and row.get("gateway_id"):
                return str(row["gateway_id"])
        except Exception:
            pass

    if family_id is not None:
        try:
            cursor.execute(
                """
                SELECT gateway_id
                FROM gateways
                WHERE family_id=%s
                ORDER BY (LOWER(status)='active') DESC, initialized_at DESC, created_at DESC
                LIMIT 1
                """,
                (int(family_id),),
            )
            row = _fetchone(cursor)
            if row and row.get("gateway_id"):
                return str(row["gateway_id"])
        except Exception:
            pass
    return None
