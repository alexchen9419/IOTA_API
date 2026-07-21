#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
UC2.3 終端設備除役與安全解綁（不含身分驗證、不含 device_credentials 版）

功能重點：
1. 停用指定設備，不做 Token、密碼或角色驗證。
2. 不刪除 devices 資料列，改為 status=Revoked、pairing_status=unpaired。
3. 清空 session_key_hash，表示設備目前信任鏈與會話金鑰失效。
4. 寫入 audit_logs，使用 prev_hash + current_hash 保留鏈式稽核紀錄。
5. 不建立、不查詢、不更新 device_credentials 表。

測試：
printf '{"payload":{"device_id":"ESP32_LOCK_001","reason":"汰換舊設備","operator_user_id":"admin001"}}' \
  | python -u cgi-bin/decommission_device.py 2>&1
"""

import hashlib
import json
import os
import re
import sys
import time
import uuid
from typing import Any, Dict, Optional

import pymysql
from dotenv import load_dotenv

load_dotenv()
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "vboxuser")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME", "devicemanagement")

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")


def response_json(data: Dict[str, Any], status_code: int = 200) -> None:
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8")
    print("Access-Control-Allow-Origin: *")
    print("Access-Control-Allow-Methods: GET, POST, OPTIONS")
    print("Access-Control-Allow-Headers: Content-Type\n")
    print(json.dumps(data, ensure_ascii=False, default=str))
    sys.exit()


def get_conn():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )


def table_exists(cursor, table_name: str) -> bool:
    cursor.execute("SHOW TABLES LIKE %s", (table_name,))
    return cursor.fetchone() is not None


def get_columns(cursor, table_name: str) -> set[str]:
    cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
    return {row["Field"] for row in cursor.fetchall()}


def stable_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def build_hash(command_id: str, operator_user_id: Optional[str], device_id: Optional[str], action: str,
               parameters: Dict[str, Any], status: str, timestamp: int, prev_hash: Optional[str]) -> str:
    raw = stable_json({
        "command_id": command_id,
        "operator_user_id": operator_user_id,
        "device_id": device_id,
        "action": action,
        "parameters": parameters,
        "status": status,
        "timestamp": timestamp,
        "prev_hash": prev_hash,
    })
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def insert_audit_log(cursor, *, operator_user_id: Optional[str], device_id: Optional[str],
                     action: str, parameters: Dict[str, Any], status: str) -> Dict[str, Any]:
    """寫入 audit_logs；若舊資料庫沒有 audit_logs，略過但不中斷 UC2.3 主功能。"""
    if not table_exists(cursor, "audit_logs"):
        return {"written": False, "reason": "audit_logs table not found"}

    audit_cols = get_columns(cursor, "audit_logs")
    timestamp = int(time.time())
    safe_action = re.sub(r"[^A-Za-z0-9_.:-]", "_", action)[:32]
    command_id = f"{safe_action}-{timestamp}-{uuid.uuid4().hex[:8]}"

    cursor.execute("SELECT current_hash FROM audit_logs ORDER BY `timestamp` DESC LIMIT 1")
    last_log = cursor.fetchone()
    prev_hash = last_log["current_hash"] if last_log and last_log.get("current_hash") else "0" * 64
    current_hash = build_hash(command_id, operator_user_id, device_id, action, parameters, status, timestamp, prev_hash)

    insert_cols: list[str] = []
    values: list[Any] = []

    def add_if_exists(col: str, value: Any) -> None:
        if col in audit_cols:
            insert_cols.append(col)
            values.append(value)

    add_if_exists("command_id", command_id)
    add_if_exists("user_id", operator_user_id)
    # 不使用 u_id，避免把 UC2.3 實作綁死在使用者身分驗證流程上。
    add_if_exists("device_id", device_id)
    add_if_exists("action", action)
    add_if_exists("parameters", stable_json(parameters))
    add_if_exists("status", status)
    add_if_exists("timestamp", timestamp)
    add_if_exists("prev_hash", prev_hash)
    add_if_exists("current_hash", current_hash)

    if not insert_cols:
        return {"written": False, "reason": "audit_logs has no supported columns"}

    placeholders = ", ".join(["%s"] * len(insert_cols))
    col_sql = ", ".join([f"`{c}`" for c in insert_cols])
    cursor.execute(f"INSERT INTO audit_logs ({col_sql}) VALUES ({placeholders})", values)

    return {
        "written": True,
        "command_id": command_id,
        "prev_hash": prev_hash,
        "current_hash": current_hash,
        "timestamp": timestamp,
    }


def normalize_payload(raw_data: str) -> Dict[str, Any]:
    if not raw_data:
        response_json({"status": "Error", "msg": "無輸入資料"}, 400)
    try:
        request_data = json.loads(raw_data)
    except json.JSONDecodeError:
        response_json({"status": "Error", "msg": "JSON 格式錯誤"}, 400)
    payload = request_data.get("payload", request_data)
    if not isinstance(payload, dict):
        response_json({"status": "Error", "msg": "payload 必須是物件"}, 400)
    return payload


def main() -> None:
    payload = normalize_payload(sys.stdin.read())
    sys.stderr.write(f"\n[DEBUG] 收到 UC2.3 除役請求 payload: {json.dumps(payload, ensure_ascii=False)}\n")

    device_id = str(payload.get("device_id") or "").strip()
    operator_user_id = payload.get("operator_user_id") or payload.get("admin_user_id") or payload.get("user_id")
    reason = str(payload.get("reason") or "UC2.3 device decommission").strip()

    if not device_id:
        response_json({"status": "Error", "msg": "欄位不齊全：需要 device_id"}, 400)

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            if not table_exists(cursor, "devices"):
                response_json({"status": "Error", "msg": "找不到 devices 資料表"}, 500)

            device_cols = get_columns(cursor, "devices")
            cursor.execute("SELECT * FROM devices WHERE device_id = %s FOR UPDATE", (device_id,))
            device = cursor.fetchone()

            if not device:
                audit = insert_audit_log(
                    cursor,
                    operator_user_id=operator_user_id,
                    device_id=None,
                    action="UC2.3_DEVICE_DECOMMISSION_DENIED",
                    parameters={"target_device_id": device_id, "reason": reason, "deny_reason": "device_not_found"},
                    status="Denied",
                )
                conn.commit()
                response_json({"status": "Error", "msg": f"找不到設備：{device_id}", "audit": audit}, 404)

            old_status = str(device.get("status") or "")
            old_pairing_status = str(device.get("pairing_status") or "")
            already_revoked = old_status.lower() in {"revoked", "retired", "decommissioned"}

            if already_revoked:
                audit = insert_audit_log(
                    cursor,
                    operator_user_id=operator_user_id,
                    device_id=device_id,
                    action="UC2.3_DEVICE_DECOMMISSION_NOOP",
                    parameters={"reason": reason, "old_status": old_status, "old_pairing_status": old_pairing_status},
                    status="Verified",
                )
                conn.commit()
                response_json({
                    "status": "Success",
                    "msg": "設備已是除役/撤銷狀態，未重複更新",
                    "data": {
                        "device_id": device_id,
                        "previous_status": old_status,
                        "new_status": old_status,
                        "previous_pairing_status": old_pairing_status,
                        "audit": audit,
                    },
                })

            update_fields: list[str] = []
            values: list[Any] = []

            def set_if_exists(col: str, value: Any, raw_sql: bool = False) -> None:
                if col in device_cols:
                    if raw_sql:
                        update_fields.append(f"`{col}` = {value}")
                    else:
                        update_fields.append(f"`{col}` = %s")
                        values.append(value)

            set_if_exists("status", "Revoked")
            set_if_exists("pairing_status", "unpaired")
            set_if_exists("session_key_hash", None)
            set_if_exists("last_action", "UC2.3_DEVICE_DECOMMISSION")
            set_if_exists("revoked_at", "NOW()", raw_sql=True)
            if operator_user_id:
                set_if_exists("revoked_by", operator_user_id)
            set_if_exists("revocation_reason", reason)

            if update_fields:
                values.append(device_id)
                cursor.execute(f"UPDATE devices SET {', '.join(update_fields)} WHERE device_id = %s", values)

            if table_exists(cursor, "device_telemetry"):
                telemetry_cols = get_columns(cursor, "device_telemetry")
                if {"device_id", "status", "telemetry_data"}.issubset(telemetry_cols):
                    cursor.execute(
                        "INSERT INTO device_telemetry (device_id, status, telemetry_data) VALUES (%s, %s, %s)",
                        (device_id, "Revoked", stable_json({"event": "UC2.3_DEVICE_DECOMMISSION", "reason": reason})),
                    )

            audit_params = {
                "reason": reason,
                "operator_user_id": operator_user_id,
                "old_status": old_status,
                "new_status": "Revoked",
                "old_pairing_status": old_pairing_status,
                "new_pairing_status": "unpaired" if "pairing_status" in device_cols else None,
                "session_key_hash_revoked": "session_key_hash" in device_cols,
            }
            audit = insert_audit_log(
                cursor,
                operator_user_id=operator_user_id,
                device_id=device_id,
                action="UC2.3_DEVICE_DECOMMISSION",
                parameters=audit_params,
                status="Verified",
            )

            conn.commit()
            response_json({
                "status": "Success",
                "msg": "UC2.3 終端設備除役與安全解綁完成（不含身分驗證、不含 device_credentials 版）",
                "data": {
                    "device_id": device_id,
                    "previous_status": old_status,
                    "new_status": "Revoked",
                    "previous_pairing_status": old_pairing_status,
                    "new_pairing_status": "unpaired" if "pairing_status" in device_cols else None,
                    "session_key_hash_revoked": "session_key_hash" in device_cols,
                    "audit": audit,
                },
            })

    except pymysql.err.DataError as e:
        conn.rollback()
        response_json({
            "status": "Error",
            "msg": "資料庫欄位值不相容，請先執行 UC2.3 migration SQL",
            "detail": str(e),
        }, 400)
    except pymysql.err.IntegrityError as e:
        conn.rollback()
        response_json({
            "status": "Error",
            "msg": "資料庫約束失敗，請確認 devices 與 audit_logs 的外鍵資料是否一致",
            "detail": str(e),
        }, 400)
    except Exception as e:
        conn.rollback()
        response_json({"status": "Error", "msg": "伺服器內部錯誤", "detail": str(e)}, 500)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
