#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared audit-log (hash chain) writer for every API endpoint.

All writers of ``audit_logs`` must go through :func:`append_audit_log` so that
(1) every new row uses one hash formula and (2) the chain head is read under a
lock inside the caller's transaction, which keeps concurrent writers from
reading the same ``prev_hash`` and forking the chain.

The module never opens connections, commits or rolls back: the caller passes the
cursor of its own transaction and commits after its business writes, so the
audit row and the business update succeed or fail together.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional

GENESIS_HASH = "0" * 64
CHAIN_LOCK_TABLE = "audit_chain_lock"

# Sentinel: "user_id column mirrors actor_id" (the historical behaviour).
_SAME_AS_ACTOR: Any = object()


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


def _lock_chain(cursor) -> None:
    """Serialise chain appends until the caller's transaction ends.

    Preferred: an exclusive row lock on the single row of ``audit_chain_lock``
    (see schema.sql / migrations). Concurrent writers queue on that row instead
    of deadlocking on audit_logs gap locks. On an older DB without the table the
    ``FOR UPDATE`` read in :func:`_previous_hash` is the only protection.
    """
    cursor.execute("SHOW TABLES LIKE %s", (CHAIN_LOCK_TABLE,))
    if not cursor.fetchone():
        return
    cursor.execute(f"SELECT id FROM `{CHAIN_LOCK_TABLE}` WHERE id = 1 FOR UPDATE")
    if cursor.fetchone():
        return
    cursor.execute(f"INSERT IGNORE INTO `{CHAIN_LOCK_TABLE}` (id) VALUES (1)")
    cursor.execute(f"SELECT id FROM `{CHAIN_LOCK_TABLE}` WHERE id = 1 FOR UPDATE")
    cursor.fetchone()


def _previous_hash(cursor, columns: dict[str, str]) -> str:
    hash_col = "current_hash" if "current_hash" in columns else ("hash" if "hash" in columns else None)
    if not hash_col:
        return GENESIS_HASH
    order_col = "id" if "id" in columns else ("timestamp" if "timestamp" in columns else hash_col)
    # Locking read: always sees the latest committed head (a plain SELECT may
    # return a stale REPEATABLE READ snapshot) and holds it until commit.
    cursor.execute(
        f"SELECT `{hash_col}` AS h FROM audit_logs WHERE `{hash_col}` IS NOT NULL "
        f"ORDER BY `{order_col}` DESC LIMIT 1 FOR UPDATE"
    )
    row = cursor.fetchone()
    return str(row.get("h")) if isinstance(row, dict) and row.get("h") else GENESIS_HASH


def build_hash_material(
    *,
    command_id: str,
    actor_id: Optional[str],
    actor_type: str,
    family_id: Optional[int],
    device_id: Optional[str],
    action: str,
    parameters: Dict[str, Any],
    status: str,
    decision: str,
    reason: Optional[str],
    timestamp: Any,
    prev_hash: str,
    u_id: Optional[int] = None,
    raw_data: Optional[Dict[str, Any]] = None,
    hash_raw_data: bool = False,
) -> Dict[str, Any]:
    """The exact dict that is hashed. Optional keys appear only when used, so
    callers that do not pass them get the original (pre-extension) hash."""
    material: Dict[str, Any] = {
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
    if u_id is not None:
        material["u_id"] = u_id
    if hash_raw_data:
        material["raw_data"] = raw_data if raw_data is not None else parameters
    return material


def compute_current_hash(material: Dict[str, Any]) -> str:
    return hashlib.sha256(_json(material).encode("utf-8")).hexdigest()


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
    command_id: Optional[str] = None,
    user_id: Any = _SAME_AS_ACTOR,
    u_id: Optional[int] = None,
    hash_raw_data: bool = False,
) -> Dict[str, Any]:
    """Append one row to the audit_logs hash chain using the caller's cursor.

    Extra keyword arguments are optional and backward compatible:
    - command_id: keep an endpoint's own id format (default ``AUDIT_<uuid>``).
    - user_id: value for the ``user_id`` column (default: same as actor_id).
    - u_id: users.id, written to ``u_id`` and covered by the hash when given.
    - hash_raw_data: also cover raw_data with the hash (for endpoints whose
      raw_data is the primary evidence, e.g. control commands).
    """
    columns = _columns(cursor, "audit_logs")
    _lock_chain(cursor)
    prev_hash = _previous_hash(cursor, columns)
    command_id = command_id or f"AUDIT_{uuid.uuid4().hex}"
    created_at = dt.datetime.utcnow().replace(microsecond=0)
    timestamp_type = columns.get("timestamp", "")
    timestamp: Any = int(time.time()) if any(x in timestamp_type for x in ("int", "decimal", "float", "double")) else created_at
    raw_value = raw_data if raw_data is not None else parameters

    material = build_hash_material(
        command_id=command_id,
        actor_id=actor_id,
        actor_type=actor_type,
        family_id=family_id,
        device_id=device_id,
        action=action,
        parameters=parameters,
        status=status,
        decision=decision,
        reason=reason,
        timestamp=timestamp,
        prev_hash=prev_hash,
        u_id=u_id,
        raw_data=raw_value,
        hash_raw_data=hash_raw_data,
    )
    current_hash = compute_current_hash(material)

    data: Dict[str, Any] = {
        "command_id": command_id,
        "user_id": actor_id if user_id is _SAME_AS_ACTOR else user_id,
        "u_id": u_id,
        "actor_id": actor_id,
        "actor_type": actor_type,
        "device_id": device_id,
        "family_id": family_id,
        "action": action,
        "parameters": _json(parameters),
        "raw_data": _json(raw_value),
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


def verify_chain_links(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Check prev_hash linkage of rows ordered by id ascending.

    Only linkage is checked, so rows written by any historical formula and rows
    written by this module validate together. Returns the list of breaks
    (empty list == intact chain).
    """
    breaks: List[Dict[str, Any]] = []
    expected: Optional[str] = None
    for row in rows:
        current = row.get("current_hash") or row.get("hash")
        prev = row.get("prev_hash")
        if expected is not None and prev != expected:
            breaks.append({"id": row.get("id"), "command_id": row.get("command_id"),
                           "expected_prev_hash": expected, "prev_hash": prev})
        expected = current
    return breaks
