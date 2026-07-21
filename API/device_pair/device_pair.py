#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC2.1 裝置註冊與安全配對 API

本 API 對應使用案例 UC2.1：
Admin 透過 Gateway 與新終端裝置進行首次安全配對，
系統使用 ECDH 建立會話金鑰，並將裝置初始註冊狀態寫入稽核日誌。

主要流程：
1. 讀取 App / 測試程式傳入的 JSON payload。
2. 檢查必要欄位：owner_user_id、device_id、device_type。
3. Gateway 產生 ECDH 金鑰組。
4. 讀取 ESP32 傳來的 public key；若未提供，則建立模擬 ESP32 key 方便測試。
5. Gateway 與 ESP32 public key 進行 ECDH，產生 shared secret。
6. 使用 HKDF 將 shared secret 轉成 session key。
7. 只儲存 session_key_hash，不直接儲存明文 session key。
8. 檢查該 device_id 是否已被 UC2.3 除役；若已除役則拒絕重新配對。
9. 將裝置資料寫入 devices 表，並保持 UC2.3 除役欄位為 NULL。
10. 將 DEVICE_REGISTERED 事件寫入 audit_logs 表，形成 prev_hash/current_hash 鏈式紀錄。

POST JSON 格式：
{
  "payload": {
    "owner_user_id": "admin001",
    "family_id": 1,
    "gateway_id": "GW_001",
    "device_id": "ESP32_LOCK_001",
    "device_name": "客廳門鎖",
    "device_type": "smart_lock",
    "device_public_key_pem": "-----BEGIN PUBLIC KEY-----..."  // 可省略，省略時使用模擬 ESP32 key
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
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# 讀取 .env 檔案中的 MySQL 連線設定。
# 例如：DB_HOST、DB_USER、DB_PASS、DB_NAME。
load_dotenv()
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "vboxuser")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME", "devicemanagement")

# CGI 在處理中文輸入/輸出時，明確指定 UTF-8 可避免亂碼。
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")


def response_json(data: Dict[str, Any], status_code: int = 200) -> None:
    """
    統一輸出 JSON 回應。

    CGI 程式必須先印出 HTTP header，再印出 body。
    Flutter App 或測試程式會讀取此 JSON 判斷 API 是否成功。
    """
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8")
    print("Access-Control-Allow-Origin: *")
    print("Access-Control-Allow-Methods: GET, POST, OPTIONS")
    print("Access-Control-Allow-Headers: Content-Type, Authorization\n")
    print(json.dumps(data, ensure_ascii=False))
    sys.exit()


def get_conn():
    """
    建立 MySQL 連線。

    autocommit=False 表示後面必須手動 conn.commit()。
    這樣可以確保 devices 與 audit_logs 兩個寫入動作要嘛一起成功，要嘛一起失敗。
    """
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def public_key_to_pem(public_key) -> str:
    """
    將 ECDH public key 轉成 PEM 字串。

    PEM 是常見的金鑰文字格式，方便存在資料庫，也方便透過 JSON 傳輸。
    """
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


def load_public_key_from_pem(pem: str):
    """
    將 PEM 格式的 public key 字串還原成 cryptography 可使用的 public key 物件。
    """
    return serialization.load_pem_public_key(pem.encode("utf-8"))


def derive_session_key(shared_secret: bytes, device_id: str, gateway_id: str) -> bytes:
    """
    使用 HKDF 從 ECDH shared secret 派生出 32 bytes session key。

    ECDH 產生的是 shared secret，通常不會直接拿來當加密金鑰，
    而是再透過 KDF（Key Derivation Function）轉成正式會話金鑰。

    info 中加入 gateway_id 與 device_id，讓不同 Gateway / 裝置組合產生的 key 彼此區隔。
    """
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=f"SeniorProject-UC2.1:{gateway_id}:{device_id}".encode("utf-8"),
    ).derive(shared_secret)


def build_ecdh_result(device_id: str, gateway_id: str, device_public_key_pem: Optional[str]) -> Dict[str, Any]:
    """
    執行 UC2.1 的 ECDH 配對核心流程。

    參數：
    - device_id：ESP32 裝置 ID。
    - gateway_id：Gateway ID。
    - device_public_key_pem：ESP32 傳來的 public key。若為 None，代表測試時沒有真 ESP32，
      系統會自動建立模擬 ESP32 金鑰。

    回傳：
    - simulated_device：是否使用模擬 ESP32 key。
    - device_side_verified：若是模擬 ESP32，會反向驗證雙方算出的 session key 是否一致。
    - device_public_key_pem：ESP32 public key。
    - gateway_public_key_pem：Gateway public key。
    - session_key_hash：session key 的 SHA-256 雜湊值。
    """
    # Gateway 端產生 ECDH private/public key。
    # SECP256R1 又稱 P-256，是常見且效能足夠的橢圓曲線。
    gateway_private_key = ec.generate_private_key(ec.SECP256R1())
    gateway_public_key_pem = public_key_to_pem(gateway_private_key.public_key())

    simulated = False
    simulated_device_private_key = None

    if device_public_key_pem:
        # 正式情境：ESP32 已經把自己的 public key 傳給 Gateway。
        device_public_key = load_public_key_from_pem(device_public_key_pem)
    else:
        # 測試情境：尚未接真 ESP32 時，API 直接模擬一台 ESP32。
        # 這樣可以先測通 App → API → DB → audit_logs 的完整流程。
        simulated = True
        simulated_device_private_key = ec.generate_private_key(ec.SECP256R1())
        device_public_key = simulated_device_private_key.public_key()
        device_public_key_pem = public_key_to_pem(device_public_key)

    # Gateway 用自己的 private key 與 ESP32 public key 計算 shared secret。
    shared_secret = gateway_private_key.exchange(ec.ECDH(), device_public_key)

    # 將 shared secret 經 HKDF 派生成正式 session key。
    session_key = derive_session_key(shared_secret, device_id, gateway_id)

    # 為了避免資料庫保存明文 session key，這裡只保存 session key 的 hash。
    session_key_hash = hashlib.sha256(session_key).hexdigest()

    device_side_verified = None
    if simulated_device_private_key is not None:
        # 只有在模擬 ESP32 時，API 才擁有裝置端 private key，因此可以做反向驗證。
        # 驗證目標：ESP32 private key + Gateway public key 算出的 session key
        # 必須和 Gateway 端算出的 session key 相同。
        gateway_public_key = load_public_key_from_pem(gateway_public_key_pem)
        device_side_shared = simulated_device_private_key.exchange(ec.ECDH(), gateway_public_key)
        device_side_session = derive_session_key(device_side_shared, device_id, gateway_id)
        device_side_verified = hashlib.sha256(device_side_session).hexdigest() == session_key_hash

    return {
        "simulated_device": simulated,
        "device_side_verified": device_side_verified,
        "device_public_key_pem": device_public_key_pem,
        "gateway_public_key_pem": gateway_public_key_pem,
        "session_key_hash": session_key_hash,
    }


def get_user_auto_id(cursor, user_id: str) -> Optional[int]:
    """
    用 users.user_id 查詢 users.id。

    audit_logs 同時保存：
    - user_id：例如 admin001，方便人類閱讀。
    - u_id：資料庫內部自動遞增 ID，方便資料表關聯。

    若目前測試資料庫沒有該使用者，仍允許配對繼續進行，u_id 會是 None。
    """
    cursor.execute("SELECT id FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    return row["id"] if row else None


def get_prev_hash(cursor) -> Optional[str]:
    """
    取得 audit_logs 中最新一筆 current_hash。

    新日誌會把這個值存進 prev_hash，形成鏈式結構：
    前一筆 current_hash → 下一筆 prev_hash。
    """
    cursor.execute("SELECT current_hash FROM audit_logs ORDER BY timestamp DESC, command_id DESC LIMIT 1")
    row = cursor.fetchone()
    return row["current_hash"] if row and row.get("current_hash") else None


def append_audit_log(cursor, owner_user_id: str, u_id: Optional[int], device_id: str, parameters: Dict[str, Any]) -> Dict[str, str]:
    """
    將 UC2.1 裝置註冊事件寫入 audit_logs。

    這裡用簡化版 hash chain 模擬「公有鏈稽核紀錄」：
    - prev_hash：上一筆日誌的 current_hash。
    - current_hash：把本筆交易內容排序後做 SHA-256。

    若未來要改成真正 IOTA 或區塊鏈節點，可從這個函式替換底層寫入方式。
    """
    command_id = f"tx-{uuid.uuid4().hex}"
    timestamp = int(time.time())
    prev_hash = get_prev_hash(cursor)

    # current_hash 的來源資料。
    # sort_keys=True 可以確保相同內容每次產生一致的 JSON 字串，避免 hash 因欄位順序不同而改變。
    hash_payload = {
        "command_id": command_id,
        "user_id": owner_user_id,
        "u_id": u_id,
        "device_id": device_id,
        "action": "DEVICE_REGISTERED",
        "parameters": parameters,
        "status": "Verified",
        "timestamp": timestamp,
        "prev_hash": prev_hash,
    }
    current_hash = hashlib.sha256(
        json.dumps(hash_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    # 將交易紀錄寫入 audit_logs。
    # parameters 使用 MySQL JSON 欄位，因此透過 CAST(%s AS JSON) 寫入。
    cursor.execute(
        """
        INSERT INTO audit_logs
          (command_id, user_id, u_id, device_id, action, parameters, status, timestamp, prev_hash, current_hash)
        VALUES
          (%s, %s, %s, %s, %s, CAST(%s AS JSON), %s, %s, %s, %s)
        """,
        (
            command_id,
            owner_user_id,
            u_id,
            device_id,
            "DEVICE_REGISTERED",
            json.dumps(parameters, ensure_ascii=False),
            "Verified",
            timestamp,
            prev_hash,
            current_hash,
        ),
    )

    return {"command_id": command_id, "prev_hash": prev_hash, "current_hash": current_hash}


def main() -> None:
    """
    API 主流程。

    此函式負責串接：
    1. 讀取請求。
    2. ECDH 配對。
    3. 寫入 devices。
    4. 寫入 audit_logs。
    5. 回傳 JSON 給 App。
    """
    try:
        # CGI 的 POST body 會從 stdin 讀入。
        raw_data = sys.stdin.read()
        if not raw_data:
            response_json({"status": "Error", "msg": "無輸入資料"}, 400)

        request_data = json.loads(raw_data)
        payload = request_data.get("payload", {})

        # 支援 owner_user_id 或 user_id，方便前端不同階段串接。
        owner_user_id = (payload.get("owner_user_id") or payload.get("user_id") or "").strip()
        gateway_id = (payload.get("gateway_id") or "GW_001").strip()
        device_id = (payload.get("device_id") or "").strip()
        device_name = (payload.get("device_name") or "未命名裝置").strip()
        device_type = (payload.get("device_type") or "smart_lock").strip()
        family_id = payload.get("family_id")
        device_public_key_pem = payload.get("device_public_key_pem")

        # UC2.1 最低必要資料。
        # family_id、gateway_id、device_name 有預設或可為空，但 owner_user_id / device_id / device_type 不可缺少。
        if not owner_user_id or not device_id or not device_type:
            response_json({"status": "Error", "msg": "owner_user_id、device_id、device_type 為必填"}, 400)

        # 執行 ECDH 配對，得到 public key、session_key_hash 等安全資料。
        ecdh_result = build_ecdh_result(device_id, gateway_id, device_public_key_pem)

        # audit_logs 不直接保存完整 public key 到 parameters，改存 public key hash，降低日誌內容大小。
        device_public_key_hash = hashlib.sha256(ecdh_result["device_public_key_pem"].encode("utf-8")).hexdigest()
        gateway_public_key_hash = hashlib.sha256(ecdh_result["gateway_public_key_pem"].encode("utf-8")).hexdigest()

        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                # 查詢使用者內部 ID；查不到時不阻擋測試流程。
                u_id = get_user_auto_id(cursor, owner_user_id)

                # UC2.3 相容性檢查：已除役設備不可由 UC2.1 直接重新配對。
                # 避免被標記為 Revoked 的裝置重新取得 session_key_hash，造成除役後信任鏈被繞過。
                cursor.execute("SELECT status, pairing_status FROM devices WHERE device_id = %s", (device_id,))
                existing_device = cursor.fetchone()
                if existing_device and str(existing_device.get("status") or "").lower() in {"revoked", "retired", "decommissioned"}:
                    response_json(
                        {
                            "status": "Error",
                            "msg": "此設備已除役，不可直接重新配對；請更換 device_id 或先建立正式重新啟用流程",
                            "data": {
                                "device_id": device_id,
                                "current_status": existing_device.get("status"),
                                "current_pairing_status": existing_device.get("pairing_status"),
                            },
                        },
                        409,
                    )

                # 將裝置資料寫入 devices。
                # 若同一個 device_id 已存在且尚未除役，使用 ON DUPLICATE KEY UPDATE 更新為最新配對資料。
                # revoked_at / revoked_by / revocation_reason 是 UC2.3 除役欄位；UC2.1 成功配對時保持為 NULL。
                cursor.execute(
                    """
                    INSERT INTO devices
                      (device_id, device_type, status, last_action, device_name, family_id,
                       gateway_id, owner_user_id, device_public_key, gateway_public_key,
                       session_key_hash, pairing_status, paired_at, revoked_at, revoked_by, revocation_reason)
                    VALUES
                      (%s, %s, 'Active', 'DEVICE_REGISTERED', %s, %s,
                       %s, %s, %s, %s, %s, 'paired', NOW(), NULL, NULL, NULL)
                    ON DUPLICATE KEY UPDATE
                      device_type = VALUES(device_type),
                      status = 'Active',
                      last_action = 'DEVICE_REGISTERED',
                      device_name = VALUES(device_name),
                      family_id = VALUES(family_id),
                      gateway_id = VALUES(gateway_id),
                      owner_user_id = VALUES(owner_user_id),
                      device_public_key = VALUES(device_public_key),
                      gateway_public_key = VALUES(gateway_public_key),
                      session_key_hash = VALUES(session_key_hash),
                      pairing_status = 'paired',
                      paired_at = NOW(),
                      revoked_at = NULL,
                      revoked_by = NULL,
                      revocation_reason = NULL
                    """,
                    (
                        device_id,
                        device_type,
                        device_name,
                        family_id,
                        gateway_id,
                        owner_user_id,
                        ecdh_result["device_public_key_pem"],
                        ecdh_result["gateway_public_key_pem"],
                        ecdh_result["session_key_hash"],
                    ),
                )

                # 準備寫入 audit_logs 的參數。
                # 這些資料會成為 DEVICE_REGISTERED 交易的一部分，也會參與 current_hash 計算。
                audit_parameters = {
                    "uc": "UC2.1",
                    "gateway_id": gateway_id,
                    "family_id": family_id,
                    "device_name": device_name,
                    "device_type": device_type,
                    "pairing_status": "paired",
                    "ecdh_curve": "secp256r1",
                    "device_public_key_hash": device_public_key_hash,
                    "gateway_public_key_hash": gateway_public_key_hash,
                    "session_key_hash": ecdh_result["session_key_hash"],
                    "simulated_device": ecdh_result["simulated_device"],
                }

                # 寫入 UC2.1 上鏈稽核紀錄。
                tx = append_audit_log(cursor, owner_user_id, u_id, device_id, audit_parameters)

                # devices 與 audit_logs 都成功後才 commit。
                conn.commit()

            # 回傳 App 顯示用結果。
            response_json(
                {
                    "status": "Success",
                    "msg": "裝置註冊與安全配對成功，初始狀態已寫入公有鏈稽核日誌",
                    "data": {
                        "device_id": device_id,
                        "device_name": device_name,
                        "device_type": device_type,
                        "gateway_id": gateway_id,
                        "owner_user_id": owner_user_id,
                        "pairing_status": "paired",
                        "ecdh_curve": "secp256r1",
                        "simulated_device": ecdh_result["simulated_device"],
                        "device_side_verified": ecdh_result["device_side_verified"],
                        "device_public_key_hash": device_public_key_hash,
                        "gateway_public_key_hash": gateway_public_key_hash,
                        "session_key_hash": ecdh_result["session_key_hash"],
                        "ledger": tx,
                    },
                }
            )
        finally:
            conn.close()

    except json.JSONDecodeError:
        response_json({"status": "Error", "msg": "JSON 格式錯誤"}, 400)
    except Exception as e:
        # 測試階段保留 detail，方便除錯。
        # 正式部署時可以移除 detail，避免暴露伺服器內部資訊。
        response_json({"status": "Error", "msg": "伺服器內部錯誤", "detail": str(e)}, 500)


if __name__ == "__main__":
    main()
