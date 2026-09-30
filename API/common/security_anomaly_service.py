"""Shared UC4.4 validation and transactional anomaly recording.

The caller supplies a dedicated, non-autocommit connection and closes it.
This service commits all three records together or rolls back on failure.
It does not publish MQTT messages or submit transactions to IOTA.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from typing import Any, Dict, Optional

from common.audit_log_service import append_audit_log
from common.ledger_event_service import (
    canonical_json, enqueue_ledger_event, get_ledger_event_by_dedup,
    hash_identifier, iso_utc, resolve_gateway_id,
)

ANOMALY_WHITELIST = {
    "ILLEGAL_CONTROL_ATTEMPT",
    "DEVICE_OFFLINE",
    "BRUTE_FORCE_ATTEMPT",
    "TOKEN_ABUSE",
    "REPLAY_ATTACK",
    "SIGNATURE_VERIFICATION_FAILED",
    "UNAUTHORIZED_DEVICE",
    "DEVICE_TAMPERING",
}
SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, detail: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail


def parse_timestamp(value: Any) -> tuple[dt.datetime, str]:
    if value in (None, ""):
        db = dt.datetime.utcnow().replace(microsecond=0)
        return db, iso_utc(db)
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApiError(400, "INVALID_TIMESTAMP", "timestamp must be ISO 8601.") from exc
    if parsed.tzinfo is not None:
        parsed_utc = parsed.astimezone(dt.timezone.utc)
        return parsed_utc.replace(tzinfo=None), iso_utc(parsed_utc)
    return parsed, iso_utc(parsed)


def hash_optional(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    text = value if isinstance(value, str) else canonical_json(value)
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def record_security_anomaly(conn, req: Dict[str, Any]) -> Dict[str, Any]:
    """Record an anomaly; deduplicate by family, request and anomaly type."""
    try:
        family_id_raw = req.get("family_id")
        if family_id_raw in (None, ""):
            raise ApiError(400, "MISSING_FIELD", "family_id is required.")
        family_id = int(family_id_raw)
        anomaly_type = str(req.get("anomaly_type") or "").strip().upper()
        if anomaly_type not in ANOMALY_WHITELIST:
            raise ApiError(400, "ANOMALY_NOT_LEDGER_ELIGIBLE", "anomaly_type is not in the ledger whitelist.")
        if req.get("is_abnormal") is False:
            raise ApiError(400, "NOT_ABNORMAL", "Only abnormal security events may be queued to the ledger.")

        severity = str(req.get("severity") or "HIGH").strip().upper()
        if severity not in SEVERITIES:
            raise ApiError(400, "INVALID_SEVERITY", "severity must be LOW, MEDIUM, HIGH or CRITICAL.")

        request_id = str(req.get("request_id") or f"REQ_{uuid.uuid4().hex}").strip()
        device_id = str(req.get("device_id") or "").strip() or None
        preferred_gateway_id = str(req.get("gateway_id") or "").strip() or None
        source = str(req.get("source") or req.get("detected_by") or "POLICY_ENGINE").strip().upper()
        occurred_at_db, occurred_at = parse_timestamp(req.get("timestamp"))

        detection_in = req.get("detection") if isinstance(req.get("detection"), dict) else {}
        source_evidence_in = req.get("source_evidence") if isinstance(req.get("source_evidence"), dict) else {}
        system_action_in = req.get("system_action") if isinstance(req.get("system_action"), dict) else {}
        notification_in = req.get("notification") if isinstance(req.get("notification"), dict) else {}
        actor_id = req.get("actor_id")
        actor_type = str(req.get("actor_type") or "UNKNOWN_OR_USER").upper()
        actor_role = str(req.get("actor_role") or "UNKNOWN").upper()

        source_ip_hash = source_evidence_in.get("source_ip_hash") or hash_optional(source_evidence_in.get("source_ip"))
        request_payload_hash = source_evidence_in.get("request_payload_hash") or hash_optional(source_evidence_in.get("request_payload"))
        evidence_hash = source_evidence_in.get("evidence_hash")
        if not evidence_hash:
            evidence_hash = hash_optional({
                "source_ip_hash": source_ip_hash,
                "request_payload_hash": request_payload_hash,
                "device_id": device_id,
                "anomaly_type": anomaly_type,
                "request_id": request_id,
            })

        dedup_key = f"UC4.4:SECURITY_ANOMALY_RECORDED:FAMILY:{family_id}:REQUEST:{request_id}:TYPE:{anomaly_type}"
        security_event_id = f"SEC_{uuid.uuid4().hex}"

        with conn.cursor() as cursor:
            gateway_id = resolve_gateway_id(
                cursor,
                family_id=family_id,
                preferred_gateway_id=preferred_gateway_id,
                device_id=device_id,
            )
            if device_id:
                cursor.execute("SELECT family_id, gateway_id FROM devices WHERE device_id=%s LIMIT 1", (device_id,))
                device = cursor.fetchone()
                if not device:
                    raise ApiError(404, "DEVICE_NOT_FOUND", "device_id does not exist.")
                if device.get("family_id") is not None and int(device["family_id"]) != family_id:
                    raise ApiError(409, "DEVICE_FAMILY_MISMATCH", "device_id does not belong to family_id.")
                if gateway_id and device.get("gateway_id") and str(device["gateway_id"]) != gateway_id:
                    raise ApiError(409, "DEVICE_GATEWAY_MISMATCH", "device_id does not belong to gateway_id.")

            cursor.execute("SELECT security_event_id FROM security_events WHERE dedup_key=%s LIMIT 1", (dedup_key,))
            existing_security = cursor.fetchone()
            if existing_security:
                ledger_event = get_ledger_event_by_dedup(cursor, dedup_key)
                conn.commit()
                return {
                    "created": False,
                    "request_id": request_id,
                    "anomaly_type": anomaly_type,
                    "security_event_id": existing_security.get("security_event_id"),
                    "audit_log": None,
                    "ledger_event": ledger_event,
                }

            cursor.execute(
                """
                INSERT INTO security_events
                  (security_event_id, dedup_key, family_id, gateway_id, device_id,
                   request_id, anomaly_type, severity, detected_by, reason_code,
                   evidence_hash, occurred_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    security_event_id,
                    dedup_key,
                    family_id,
                    gateway_id,
                    device_id,
                    request_id,
                    anomaly_type,
                    severity,
                    str(detection_in.get("detected_by") or source),
                    str(detection_in.get("reason_code") or "SECURITY_POLICY_TRIGGERED"),
                    str(evidence_hash).split(":", 1)[-1] if evidence_hash else None,
                    occurred_at_db,
                ),
            )

            audit = append_audit_log(
                cursor,
                actor_id=str(actor_id) if actor_id not in (None, "") else None,
                actor_type=actor_type,
                family_id=family_id,
                device_id=device_id,
                action="UC4.4_SECURITY_ANOMALY_RECORDED",
                parameters={
                    "request_id": request_id,
                    "anomaly_type": anomaly_type,
                    "severity": severity,
                    "reason_code": detection_in.get("reason_code"),
                    "evidence_hash": evidence_hash,
                },
                status="Verified",
                decision="DENY" if str(detection_in.get("policy_decision") or "DENY").upper() == "DENY" else "ALLOW",
                reason=str(detection_in.get("reason_code") or anomaly_type),
            )

            notified_roles_raw = notification_in.get("notified_roles")
            if not isinstance(notified_roles_raw, list) or not notified_roles_raw:
                notified_roles_raw = ["ADMIN"]

            ledger_payload = {
                "is_abnormal": True,
                "anomaly_type": anomaly_type,
                "severity": severity,
                "request_id": request_id,
                "detection": {
                    "detected_by": str(detection_in.get("detected_by") or source),
                    "policy_decision": str(detection_in.get("policy_decision") or "DENY").upper(),
                    "reason_code": str(detection_in.get("reason_code") or "SECURITY_POLICY_TRIGGERED"),
                    "attempt_count": int(detection_in.get("attempt_count") or 1),
                    "observation_window_seconds": int(detection_in.get("observation_window_seconds") or 0),
                },
                "source_evidence": {
                    "source_ip_hash": source_ip_hash,
                    "request_payload_hash": request_payload_hash,
                    "evidence_hash": evidence_hash,
                },
                "system_action": {
                    "request_blocked": bool(system_action_in.get("request_blocked", True)),
                    "temporary_lockout": bool(system_action_in.get("temporary_lockout", False)),
                    "lockout_seconds": int(system_action_in.get("lockout_seconds") or 0),
                },
                "notification": {
                    "notification_dispatched": bool(notification_in.get("notification_dispatched", False)),
                    "notified_roles": [str(x).upper() for x in notified_roles_raw],
                },
            }
            ledger_event = enqueue_ledger_event(
                cursor,
                uc_id="UC4.4",
                event_type="SECURITY_ANOMALY_RECORDED",
                dedup_key=dedup_key,
                family_id=family_id,
                gateway_id=gateway_id,
                device_id=device_id,
                created_by=str(actor_id) if actor_id not in (None, "") else None,
                source=source,
                timestamp=occurred_at,
                actor={
                    "actor_type": actor_type,
                    "actor_id_hash": hash_identifier(actor_id),
                    "actor_role": actor_role,
                },
                payload=ledger_payload,
            )
            conn.commit()

        return {
            "created": bool(ledger_event.get("created")),
            "request_id": request_id,
            "anomaly_type": anomaly_type,
            "security_event_id": security_event_id,
            "audit_log": audit,
            "ledger_event": ledger_event,
        }
    except Exception:
        conn.rollback()
        raise
