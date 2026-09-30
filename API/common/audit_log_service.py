#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small shared audit-log writer for newly integrated UC endpoints."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
import uuid
from typing import Any, Dict, Optional


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _columns(cursor, table: str) -> dict[str, str]:
    cursor.execute(f"SHOW COLUMNS FROM `{table}`")
    rows = cursor.fetchall() or []
    return {
        str(row.get("Field")): str(row.get("Type", "")).lower()
        for row in rows
        if isinstance(row, dict) and row.get("Field")
    }


def _previous_hash(cursor, columns: dict[str, str]) -> str:
    hash_col = "current_hash" if "current_hash" in columns else ("hash" if "hash" in columns else None)
    if not hash_col:
        return "0" * 64
    order_col = "id" if "id" in columns else ("timestamp" if "timestamp" in columns else hash_col)
    cursor.execute(
        f"SELECT `{hash_col}` AS h FROM audit_logs WHERE `{hash_col}` IS NOT NULL ORDER BY `{order_col}` DESC LIMIT 1"
    )
    row = cursor.fetchone()
    return str(row.get("h")) if isinstance(row, dict) and row.get("h") else "0" * 64


def append_audit_log(
    cursor,
    *,
    actor_id: Optional[str],
    actor_type: str,
    family_id: Optional[int],
    device_id: Optional[str],
    action: str,
    parameters: Dict[str, Any],
    status: str = "Verified",
    decision: str = "ALLOW",
    reason: Optional[str] = None,
    raw_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    columns = _columns(cursor, "audit_logs")
    prev_hash = _previous_hash(cursor, columns)
    command_id = f"AUDIT_{uuid.uuid4().hex}"
    created_at = dt.datetime.utcnow().replace(microsecond=0)
    timestamp_type = columns.get("timestamp", "")
    timestamp: Any = int(time.time()) if any(x in timestamp_type for x in ("int", "decimal", "float", "double")) else created_at

    hash_material = {
        "command_id": command_id,
        "actor_id": actor_id,
        "actor_type": actor_type,
        "family_id": family_id,
        "device_id": device_id,
        "action": action,
        "parameters": parameters,
        "status": status,
        "decision": decision,
        "reason": reason,
        "timestamp": str(timestamp),
        "prev_hash": prev_hash,
    }
    current_hash = hashlib.sha256(_json(hash_material).encode("utf-8")).hexdigest()

    data: Dict[str, Any] = {
        "command_id": command_id,
        "user_id": actor_id,
        "actor_id": actor_id,
        "actor_type": actor_type,
        "device_id": device_id,
        "family_id": family_id,
        "action": action,
        "parameters": _json(parameters),
        "raw_data": _json(raw_data if raw_data is not None else parameters),
        "status": status,
        "decision": decision,
        "reason": reason,
        "prev_hash": prev_hash,
        "current_hash": current_hash,
        "hash": current_hash,
        "timestamp": timestamp,
        "created_at": created_at,
    }
    data = {key: value for key, value in data.items() if key in columns}
    keys = list(data.keys())
    cursor.execute(
        f"INSERT INTO audit_logs ({', '.join(f'`{k}`' for k in keys)}) VALUES ({', '.join(['%s'] * len(keys))})",
        tuple(data[key] for key in keys),
    )
    return {
        "command_id": command_id,
        "prev_hash": prev_hash,
        "current_hash": current_hash,
        "timestamp": timestamp,
    }
