#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC2.2 設備安全韌體更新（OTA）觸發 API

Admin 指定家庭裡的某台裝置與已簽章的韌體檔名，經身分驗證後由本端點
對 mqtt-server 發布 MQTT OTA 觸發訊息（home/device/<mac>/ota），格式跟
mqtt-server/test_ota.py 手動觸發時完全一致：
{"url": "http://<OTA_HOST>:8080/firmware/<file>.bin", "version": "..."}

裝置端下載後會驗證 Ed25519 簽章（見 Arduino/MqttSmartLock/OTA.md），
通過才會 flash 並自動重開機。

本端點不檢查韌體檔案/簽章檔是否真的存在——firmware 目錄掛在 mqtt-server
容器內，這支腳本所在的 api 容器沒有掛載那個路徑；呼叫者需自行確認檔名、
且已用 sign_firmware.py 簽過名（見 mqtt-server/firmware/README.md）。

POST JSON:
{
  "payload": {
    "family_id": 1,
    "admin_uid": "admin001",
    "device_id": "E8:31:CD:82:80:C8",
    "firmware_file": "SMART-LOCK-V1_1.1.0.bin",
    "version": "1.1.0"
  }
}
"""

import hashlib
import json
import os
import sys
import time
import uuid
from typing import Any, Dict, Optional

import pymysql
from dotenv import load_dotenv
import mqtt_tls

load_dotenv()
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "vboxuser")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME", "devicemanagement")
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
# ESP32 要能直接連到的區網 IP，跟 mqtt-server/test_ota.py、Arduino sketch 的 MQTT_BROKER 是同一台機器
OTA_HOST = os.getenv("OTA_HOST", "192.168.1.3")

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")


def response_json(data: Dict[str, Any], status_code: int = 200) -> None:
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8\n")
    print(json.dumps(data, ensure_ascii=False))
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
                      status: str, decision: str, reason: str, parameters: Dict[str, Any]) -> Dict[str, str]:
    command_id = f"OTA_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    timestamp = int(time.time())
    prev_hash = get_prev_hash(cursor)
    hash_payload = {
        "command_id": command_id, "actor_id": actor_id, "device_id": device_id,
        "family_id": family_id, "action": "OTA_TRIGGERED", "parameters": parameters,
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
          (%s, %s, 'USER', %s, %s, %s, 'OTA_TRIGGERED', CAST(%s AS JSON), %s, %s, %s, %s, %s, %s, %s)
        """,
        (command_id, actor_id, actor_id, device_id, family_id,
         json.dumps(parameters, ensure_ascii=False), status, decision, reason,
         prev_hash, current_hash, current_hash, timestamp),
    )
    return {"command_id": command_id, "prev_hash": prev_hash, "current_hash": current_hash}


def main() -> None:
    try:
        raw_data = sys.stdin.read()
        if not raw_data:
            response_json({"status": "Error", "msg": "無輸入資料"}, 400)

        request_data = json.loads(raw_data)
        payload = request_data.get("payload", {})

        family_id = payload.get("family_id")
        admin_uid = str(payload.get("admin_uid") or "").strip()
        device_id = str(payload.get("device_id") or "").strip()
        firmware_file = str(payload.get("firmware_file") or "").strip()
        version = str(payload.get("version") or "dev").strip()

        if not all([family_id, admin_uid, device_id, firmware_file]):
            response_json(
                {"status": "Error", "msg": "缺少必要參數(family_id, admin_uid, device_id, firmware_file)"}, 400
            )
        if not firmware_file.endswith(".bin"):
            response_json({"status": "Error", "msg": "firmware_file 必須是 .bin 檔"}, 400)

        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                # 1. 驗證 admin_uid 是該 family 的 Admin
                cursor.execute(
                    "SELECT role FROM user_families WHERE family_id = %s AND user_id = %s",
                    (family_id, admin_uid),
                )
                role_row = cursor.fetchone()
                if not role_row or role_row["role"] != "Admin":
                    response_json({"status": "Error", "msg": "權限拒絕：只有該家庭的 Admin 才能觸發韌體更新"}, 403)

                # 2. 驗證裝置存在、屬於這個家庭、且未被除役
                cursor.execute("SELECT family_id, status FROM devices WHERE device_id = %s", (device_id,))
                device = cursor.fetchone()
                if not device:
                    response_json({"status": "Error", "msg": f"找不到裝置：{device_id}"}, 404)
                if device.get("family_id") is not None and int(device["family_id"]) != int(family_id):
                    response_json({"status": "Error", "msg": "裝置不屬於此家庭"}, 403)
                if str(device.get("status") or "").lower() in {"revoked", "retired", "decommissioned"}:
                    response_json({"status": "Error", "msg": "裝置已除役，無法觸發韌體更新"}, 409)

                # 3. 組出韌體下載網址與 topic（跟 mqtt-server/test_ota.py 一致）
                firmware_url = f"http://{OTA_HOST}:8080/firmware/{firmware_file}"
                ota_topic = f"home/device/{device_id}/ota"
                mqtt_payload = {"url": firmware_url, "version": version}

                # 4. 發布 MQTT OTA 觸發訊息
                try:
                    import paho.mqtt.client as mqtt

                    client = mqtt.Client()
                    mqtt_tls.apply_tls(client)
                    client.connect(MQTT_HOST, mqtt_tls.broker_port(), 10)
                    client.loop_start()
                    result = client.publish(ota_topic, json.dumps(mqtt_payload), qos=1)
                    result.wait_for_publish(timeout=5)
                    client.loop_stop()
                    client.disconnect()
                    if result.rc != mqtt.MQTT_ERR_SUCCESS:
                        raise RuntimeError(f"MQTT publish rc={result.rc}")
                except Exception as mqtt_err:
                    audit = append_audit_log(
                        cursor, actor_id=admin_uid, device_id=device_id, family_id=family_id,
                        status="Failed", decision="ALLOW", reason=f"MQTT_PUBLISH_FAILED: {mqtt_err}",
                        parameters={"firmware_file": firmware_file, "version": version, "target_topic": ota_topic},
                    )
                    conn.commit()
                    response_json(
                        {"status": "Error", "msg": "MQTT 發布失敗，韌體更新未觸發", "detail": str(mqtt_err), "audit": audit},
                        502,
                    )

                # 5. 更新裝置最後動作、寫入稽核日誌
                cursor.execute("UPDATE devices SET last_action = 'OTA_TRIGGERED' WHERE device_id = %s", (device_id,))
                audit = append_audit_log(
                    cursor, actor_id=admin_uid, device_id=device_id, family_id=family_id,
                    status="Published", decision="ALLOW", reason="OTA_MQTT_PUBLISHED",
                    parameters={
                        "firmware_file": firmware_file, "version": version,
                        "target_topic": ota_topic, "firmware_url": firmware_url,
                    },
                )
                conn.commit()

            response_json({
                "status": "Success",
                "msg": "韌體更新已透過 MQTT 觸發，裝置將開始下載並驗證簽章",
                "data": {
                    "device_id": device_id,
                    "family_id": family_id,
                    "firmware_file": firmware_file,
                    "version": version,
                    "target_topic": ota_topic,
                    "firmware_url": firmware_url,
                    "audit": audit,
                },
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
