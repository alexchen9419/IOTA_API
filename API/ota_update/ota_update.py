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

觸發前本端點會檢查：檔名只能是簡單的 *.bin（不得含路徑字元）、檔案存在於
firmware 目錄、旁邊有 64 bytes 的 .sig，且用 OTA 公鑰驗證 Ed25519 簽章
（簽的是 .bin 的 SHA-256，見 mqtt-server/sign_firmware.py）。任何一項不過就不發 MQTT。
docker-compose.yml 把 ./mqtt-server/firmware 掛到 /firmware、公鑰檔掛到
/ota/ota_public_key.h（唯讀）；沒掛載就回 503，不會放行。

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
from common.safe_error import public_detail
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

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
FIRMWARE_DIR = Path(os.getenv("FIRMWARE_DIR", "/firmware"))
OTA_PUBKEY_FILE = Path(os.getenv("OTA_PUBKEY_FILE", "/ota/ota_public_key.h"))
FIRMWARE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,98}\.bin$")
VERSION_RE = re.compile(r"^[A-Za-z0-9._+-]{1,32}$")

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")


def response_json(data: Dict[str, Any], status_code: int = 200) -> None:
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8\n")
    print(json.dumps(data, ensure_ascii=False))
    sys.exit()


def load_ota_public_key():
    """從 ota_public_key.h（Arduino 用的 C 陣列）解析出 32 bytes Ed25519 公鑰。"""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    text = OTA_PUBKEY_FILE.read_text(encoding="utf-8")
    raw = bytes(int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]{2})", text))
    if len(raw) != 32:
        raise ValueError("OTA public key must be 32 bytes")
    return Ed25519PublicKey.from_public_bytes(raw)


def check_firmware(firmware_file: str) -> Optional[Tuple[str, int]]:
    """檢查韌體與簽章；通過回 None，否則回 (給客戶端的訊息, HTTP 狀態碼)。細節只寫日誌。"""
    from cryptography.exceptions import InvalidSignature

    try:
        root = FIRMWARE_DIR.resolve()
        if not root.is_dir() or not OTA_PUBKEY_FILE.is_file():
            print(f"[error] firmware store not mounted: {FIRMWARE_DIR} / {OTA_PUBKEY_FILE}", file=sys.stderr)
            return "韌體庫未設定，無法驗證韌體", 503
        bin_path = (root / firmware_file).resolve()
        if bin_path.parent != root:
            return "firmware_file 不合法", 400
        if not bin_path.is_file():
            return "找不到指定的韌體檔案", 404
        sig_path = bin_path.with_name(bin_path.name + ".sig")
        if not sig_path.is_file() or sig_path.stat().st_size != 64:
            return "韌體尚未簽章（缺少有效的 .sig 檔）", 409
        digest = hashlib.sha256(bin_path.read_bytes()).digest()
        try:
            load_ota_public_key().verify(sig_path.read_bytes(), digest)
        except InvalidSignature:
            return "韌體簽章驗證失敗，拒絕觸發更新", 409
    except Exception as exc:
        public_detail(exc)
        return "韌體檢查失敗", 500
    return None


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
        if not FIRMWARE_NAME_RE.match(firmware_file):
            response_json({"status": "Error", "msg": "firmware_file 必須是簡單的 .bin 檔名（不可含路徑）"}, 400)
        if not VERSION_RE.match(version):
            response_json({"status": "Error", "msg": "version 格式不合法"}, 400)

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

                # 2.5 韌體與簽章檢查（放在權限檢查之後，非 Admin 無法探測韌體檔案是否存在）
                fw_error = check_firmware(firmware_file)
                if fw_error:
                    response_json({"status": "Error", "msg": fw_error[0]}, fw_error[1])

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
                        {"status": "Error", "msg": "MQTT 發布失敗，韌體更新未觸發", "detail": public_detail(mqtt_err), "audit": audit},
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
            response_json({"status": "Error", "msg": "資料庫操作失敗", "detail": public_detail(e)}, 500)
        finally:
            conn.close()

    except Exception as e:
        response_json({"status": "Error", "msg": "伺服器內部錯誤", "detail": public_detail(e)}, 500)


if __name__ == "__main__":
    main()
