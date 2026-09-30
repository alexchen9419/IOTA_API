#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UC4.4 security-anomaly intake API with ledger-event enqueueing."""
from __future__ import annotations

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

from common.security_anomaly_service import ApiError, record_security_anomaly

load_dotenv()
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "vboxuser")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME", "devicemanagement")

if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


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
        req = read_payload()
        conn = get_conn()
        result = record_security_anomaly(conn, req)
        created = result.pop("created")
        respond(
            201 if created else 200,
            {
                "status": "Success",
                "message": "Security anomaly recorded and ledger event queued." if created else "Security anomaly was already recorded; no duplicate audit or ledger event was created.",
                "data": result,
            },
        )
    except ApiError as exc:
        respond(exc.status_code, {"status": "Error", "code": exc.code, "message": exc.message, "detail": exc.detail})
    except json.JSONDecodeError as exc:
        respond(400, {"status": "Error", "code": "INVALID_JSON", "message": "Request body must be valid JSON.", "detail": str(exc)})
    except Exception as exc:
        detail = traceback.format_exc() if os.getenv("DEBUG", "0") == "1" else str(exc)
        respond(500, {"status": "Error", "code": "INTERNAL_ERROR", "message": "Unexpected server error.", "detail": detail})
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    main()
