#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC5.1 切換設備維修模式

Admin 手動開啟/關閉指定裝置的「維修模式」。開啟時暫停該裝置的日常遠端控制
（`control_device.py` 會拒絕 lock/unlock 等一般動作，見該檔案的
`check_maintenance_mode()`），且**強制**要求設定最長有效時間
（`duration_minutes`，不可省略、且不可超過 `MAX_MAINTENANCE_MINUTES`）。
時間到達後由 `control_device/mqtt_topic_bridge.py` 的背景 sweep 執行緒
自動強制恢復為日常安全模式，不需要任何人手動關閉或查詢才會生效。

本檔案只負責「手動切換」與「查詢目前狀態」，不涉及 UC5.1 描述裡「對外開放
受限的硬體診斷埠」（韌體/硬體層面的工作，需要實體 ESP32 才能驗證，
本次未實作）與「依設定條件觸發」（規則式自動觸發，需求未明確定義，
本次只做手動觸發）。

POST JSON（開啟）：
{
  "payload": {
    "family_id": 1,
    "admin_uid": "admin001",
    "device_id": "E8:31:CD:82:80:C8",
    "action": "Enable",
    "duration_minutes": 60,
    "reason": "更換電池"
  }
}

POST JSON（關閉，提前手動恢復）：
{
  "payload": {
    "family_id": 1,
    "admin_uid": "admin001",
    "device_id": "E8:31:CD:82:80:C8",
    "action": "Disable"
  }
}

GET 查詢目前狀態（查詢時若已過期會順便自動清除，等同 sweep 提前跑一次）：
/maintenance_mode?device_id=E8:31:CD:82:80:C8
"""

import hashlib
import json
import os
import sys
import time
import urllib.parse
import uuid
from typing import Any, Dict, Optional

import pymysql
from dotenv import load_dotenv
import mqtt_tls  # noqa: F401  # 目前未直接發 MQTT，保留匯入以維持跟其他端點一致的環境相依性檢查

load_dotenv()
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "vboxuser")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME", "devicemanagement")
MAX_MAINTENANCE_MINUTES = int(os.getenv("MAX_MAINTENANCE_MINUTES", "240"))  # 預設上限 4 小時

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")


def response_json(data: Dict[str, Any], status_code: int = 200) -> None:
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8\n")
    print(json.dumps(data, ensure_ascii=False, default=str))
    sys.exit()


def get_conn():
    return pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME,
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=False,
    )


def get_prev_hash(cursor) -> str:
    cursor.execute("SELECT current_hash FROM audit_logs ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    return row["current_hash"] if row and row.get("current_hash") else "0" * 64


def append_audit_log(cursor, *, actor_id: str, device_id: str, family_id: int,
                      action: str, status: str, decision: str, reason: str,
                      parameters: Dict[str, Any]) -> Dict[str, str]:
    command_id = f"MTN_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    timestamp = int(time.time())
    prev_hash = get_prev_hash(cursor)
    hash_payload = {
        "command_id": command_id, "actor_id": actor_id, "device_id": device_id,
        "family_id": family_id, "action": action, "parameters": parameters,
        "status": status, "timestamp": timestamp, "prev_hash": prev_hash,
    }
    current_hash = hashlib.sha256(
        json.dumps(hash_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cursor.execute(
        """
        INSERT INTO audit_logs
          (command_id, actor_id, actor_type, user_id, device_id, family_id, action,
           parameters, status, decision, reason, prev_hash, current_hash, hash, timestamp)
        VALUES
          (%s, %s, 'USER', %s, %s, %s, %s, CAST(%s AS JSON), %s, %s, %s, %s, %s, %s, %s)
        """,
        (command_id, actor_id, actor_id, device_id, family_id, action,
         json.dumps(parameters, ensure_ascii=False), status, decision, reason,
         prev_hash, current_hash, current_hash, timestamp),
    )
    return {"command_id": command_id, "prev_hash": prev_hash, "current_hash": current_hash}


def require_admin(cursor, family_id: int, admin_uid: str) -> None:
    cursor.execute(
        "SELECT role FROM user_families WHERE family_id = %s AND user_id = %s",
        (family_id, admin_uid),
    )
    role_row = cursor.fetchone()
    if not role_row or role_row["role"] != "Admin":
        response_json({"status": "Error", "msg": "權限拒絕：只有該家庭的 Admin 才能切換維修模式"}, 403)


def load_device(cursor, device_id: str, family_id: int) -> Dict[str, Any]:
    cursor.execute(
        "SELECT family_id, status, maintenance_mode, maintenance_expires_at, maintenance_reason "
        "FROM devices WHERE device_id = %s",
        (device_id,),
    )
    device = cursor.fetchone()
    if not device:
        response_json({"status": "Error", "msg": f"找不到裝置：{device_id}"}, 404)
    if device.get("family_id") is not None and int(device["family_id"]) != int(family_id):
        response_json({"status": "Error", "msg": "裝置不屬於此家庭"}, 403)
    if str(device.get("status") or "").lower() in {"revoked", "retired", "decommissioned"}:
        response_json({"status": "Error", "msg": "裝置已除役，無法切換維修模式"}, 409)
    return device


def auto_clear_if_expired(cursor, device_id: str, device: Dict[str, Any]) -> Dict[str, Any]:
    """查詢當下若維修模式已過期，順手清除（等同 sweep 提前跑一次），回傳最新狀態。"""
    if not device.get("maintenance_mode"):
        return device
    expires_at = device.get("maintenance_expires_at")
    if expires_at is None:
        return device
    cursor.execute(
        "SELECT (%s <= NOW()) AS expired, NOW() AS now_ts",
        (expires_at,),
    )
    row = cursor.fetchone()
    if not row or not row.get("expired"):
        return device
    cursor.execute(
        """
        UPDATE devices
        SET maintenance_mode = 0, maintenance_expires_at = NULL, maintenance_reason = NULL,
            last_action = 'MAINTENANCE_MODE_AUTO_EXPIRED'
        WHERE device_id = %s
        """,
        (device_id,),
    )
    device = dict(device)
    device["maintenance_mode"] = 0
    device["maintenance_expires_at"] = None
    device["maintenance_reason"] = None
    return device


def handle_query(payload: Dict[str, Any]) -> None:
    device_id = str(payload.get("device_id") or "").strip()
    if not device_id:
        response_json({"status": "Error", "msg": "缺少必要參數：device_id"}, 400)

    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT device_id, family_id, maintenance_mode, maintenance_expires_at, maintenance_reason "
                "FROM devices WHERE device_id = %s",
                (device_id,),
            )
            device = cursor.fetchone()
            if not device:
                response_json({"status": "Error", "msg": f"找不到裝置：{device_id}"}, 404)
            device = auto_clear_if_expired(cursor, device_id, device)
            conn.commit()
        response_json({
            "status": "Success",
            "data": {
                "device_id": device_id,
                "maintenance_mode": bool(device.get("maintenance_mode")),
                "maintenance_expires_at": device.get("maintenance_expires_at"),
                "maintenance_reason": device.get("maintenance_reason"),
            },
        })
    finally:
        conn.close()


def main() -> None:
    try:
        method = os.environ.get("REQUEST_METHOD", "GET").upper()

        if method == "GET":
            query = urllib.parse.parse_qs(os.environ.get("QUERY_STRING", ""))
            payload = {key: value[0] for key, value in query.items() if value}
            handle_query(payload)
            return

        raw_data = sys.stdin.read()
        if not raw_data:
            response_json({"status": "Error", "msg": "無輸入資料"}, 400)

        request_data = json.loads(raw_data)
        payload = request_data.get("payload", {})

        family_id = payload.get("family_id")
        admin_uid = str(payload.get("admin_uid") or "").strip()
        device_id = str(payload.get("device_id") or "").strip()
        action = str(payload.get("action") or "").strip().capitalize()

        if action not in {"Enable", "Disable", "Status"}:
            response_json({"status": "Error", "msg": "action 必須是 Enable、Disable 或 Status"}, 400)

        if action == "Status":
            handle_query({"device_id": device_id})
            return

        if not all([family_id, admin_uid, device_id]):
            response_json({"status": "Error", "msg": "缺少必要參數(family_id, admin_uid, device_id)"}, 400)

        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                require_admin(cursor, family_id, admin_uid)
                device = load_device(cursor, device_id, family_id)

                if action == "Enable":
                    duration_minutes = payload.get("duration_minutes")
                    if duration_minutes in (None, ""):
                        response_json(
                            {"status": "Error", "msg": "開啟維修模式必須指定 duration_minutes（系統強制要求最長有效時間）"},
                            400,
                        )
                    try:
                        duration_minutes = int(duration_minutes)
                    except (TypeError, ValueError):
                        response_json({"status": "Error", "msg": "duration_minutes 必須是整數"}, 400)
                    if duration_minutes <= 0:
                        response_json({"status": "Error", "msg": "duration_minutes 必須大於 0"}, 400)
                    if duration_minutes > MAX_MAINTENANCE_MINUTES:
                        response_json(
                            {"status": "Error",
                             "msg": f"duration_minutes 不可超過系統上限 {MAX_MAINTENANCE_MINUTES} 分鐘"},
                            400,
                        )
                    reason = str(payload.get("reason") or "").strip() or "未提供原因"

                    cursor.execute(
                        """
                        UPDATE devices
                        SET maintenance_mode = 1,
                            maintenance_expires_at = DATE_ADD(NOW(), INTERVAL %s MINUTE),
                            maintenance_reason = %s,
                            last_action = 'MAINTENANCE_MODE_ENABLED'
                        WHERE device_id = %s
                        """,
                        (duration_minutes, reason, device_id),
                    )
                    audit = append_audit_log(
                        cursor, actor_id=admin_uid, device_id=device_id, family_id=family_id,
                        action="MAINTENANCE_MODE_ENABLED", status="Active", decision="ALLOW",
                        reason=reason, parameters={"duration_minutes": duration_minutes, "reason": reason},
                    )
                    conn.commit()
                    response_json({
                        "status": "Success",
                        "msg": f"維修模式已開啟，{duration_minutes} 分鐘後自動恢復日常安全模式",
                        "data": {
                            "device_id": device_id, "family_id": family_id,
                            "duration_minutes": duration_minutes, "reason": reason, "audit": audit,
                        },
                    })

                else:  # Disable
                    if not device.get("maintenance_mode"):
                        response_json({"status": "Warning", "msg": "裝置目前不在維修模式，無需關閉"}, 200)

                    cursor.execute(
                        """
                        UPDATE devices
                        SET maintenance_mode = 0, maintenance_expires_at = NULL, maintenance_reason = NULL,
                            last_action = 'MAINTENANCE_MODE_DISABLED'
                        WHERE device_id = %s
                        """,
                        (device_id,),
                    )
                    audit = append_audit_log(
                        cursor, actor_id=admin_uid, device_id=device_id, family_id=family_id,
                        action="MAINTENANCE_MODE_DISABLED", status="Restored", decision="ALLOW",
                        reason="手動提前關閉維修模式", parameters={},
                    )
                    conn.commit()
                    response_json({
                        "status": "Success",
                        "msg": "維修模式已關閉，裝置恢復日常安全模式",
                        "data": {"device_id": device_id, "family_id": family_id, "audit": audit},
                    })

        except pymysql.MySQLError as e:
            conn.rollback()
            response_json({"status": "Error", "msg": "資料庫操作失敗", "detail": str(e)}, 500)
        finally:
            conn.close()

    except Exception as e:
        response_json({"status": "Error", "msg": "伺服器內部錯誤", "detail": str(e)}, 500)


if __name__ == "__main__":
    main()
