#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC1.3 閘道器初始化與屋主綁定共用函式。

責任邊界：
- UC1.3：建立 Gateway 長期身分、建立場域、綁定首位 Admin、建立 Genesis 待上鏈事件。
- UC2.1：Gateway 與 ESP32 的 ECDH session key 配對。
- UC1.4：不同場域 Gateway 之間的信任綁定。
- UC5.6：NAT / VPN / DDNS 等跨網路路由。

安全原則：
- Gateway private key 只存在 Gateway 本機，不寫入 MySQL。
- initialization token 明文只在 provision 時顯示一次，本機只保存 SHA-256。
- 初始化資料、audit log 與 ledger_events 在同一個 MySQL transaction 內建立。
- 尚未接入 IOTA Ledger Worker 前，Genesis 事件只標示為 PENDING，不虛構鏈上成功。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "vboxuser")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME", "devicemanagement")

BINDING_METHOD = "PHYSICAL_LOCAL_CONNECTION"
UC_ID = "UC1.3"
GENESIS_EVENT_TYPE = "SITE_GENESIS_CREATED"
GENESIS_SCHEMA_VERSION = "1.0"

try:
    INITIALIZATION_TOKEN_TTL_SECONDS = int(
        os.getenv("UC1_3_INITIALIZATION_TOKEN_TTL_SECONDS", "600")
    )
except ValueError:
    INITIALIZATION_TOKEN_TTL_SECONDS = 600
# 初始化碼為短效一次性憑證，最低 60 秒、最高 24 小時。
INITIALIZATION_TOKEN_TTL_SECONDS = max(
    60, min(INITIALIZATION_TOKEN_TTL_SECONDS, 86400)
)

_DEFAULT_STATE_DIR = Path(__file__).resolve().parent.parent / "gateway_runtime"
GATEWAY_STATE_DIR = Path(
    os.getenv("UC1_3_GATEWAY_STATE_DIR", str(_DEFAULT_STATE_DIR))
).expanduser().resolve()
PRIVATE_KEY_PATH = GATEWAY_STATE_DIR / "gateway_private_key.pem"
IDENTITY_PATH = GATEWAY_STATE_DIR / "gateway_identity.json"
BOOTSTRAP_PATH = GATEWAY_STATE_DIR / "gateway_bootstrap.json"

if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


class ApiError(Exception):
    """可預期 API 錯誤，由主程式轉為 CGI JSON response。"""

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        data: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.data = data


def response_json(data: Dict[str, Any], status_code: int = 200) -> None:
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8")
    print("Access-Control-Allow-Origin: *")
    print("Access-Control-Allow-Methods: GET, POST, OPTIONS")
    print("Access-Control-Allow-Headers: Content-Type, Authorization\n")
    print(json.dumps(data, ensure_ascii=False, default=str))
    raise SystemExit


def handle_api_error(exc: ApiError) -> None:
    body: Dict[str, Any] = {"status": "Error", "msg": exc.message}
    if exc.data is not None:
        body["data"] = exc.data
    response_json(body, exc.status_code)


def normalize_payload(raw_data: str) -> Dict[str, Any]:
    if not raw_data:
        raise ApiError("無輸入資料", 400)
    try:
        request_data = json.loads(raw_data)
    except json.JSONDecodeError:
        raise ApiError("JSON 格式錯誤", 400)

    payload = request_data.get("payload", request_data)
    if not isinstance(payload, dict):
        raise ApiError("payload 必須是 JSON object", 400)
    return payload


def stable_json(data: Any) -> str:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_naive() -> datetime:
    # 現有 MySQL schema 使用無 timezone DATETIME，統一寫 UTC naive。
    return utc_now().replace(tzinfo=None)


def iso_utc(dt: Optional[datetime] = None) -> str:
    target = dt or utc_now()
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    return target.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise ApiError("Gateway bootstrap 的時間格式無效，請重新 provision", 409)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_conn():
    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError("缺少 pymysql，請先安裝專案必要套件") from exc

    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def ensure_state_dir(state_dir: Optional[Path] = None) -> Path:
    path = (state_dir or GATEWAY_STATE_DIR).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def state_paths(state_dir: Optional[Path] = None) -> Tuple[Path, Path, Path]:
    base = ensure_state_dir(state_dir)
    return (
        base / "gateway_private_key.pem",
        base / "gateway_identity.json",
        base / "gateway_bootstrap.json",
    )


def atomic_write_json(path: Path, data: Dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load_json_file(path: Path, label: str) -> Dict[str, Any]:
    if not path.exists():
        raise ApiError(f"找不到 {label}：{path}", 409)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ApiError(f"{label} 無法讀取或格式錯誤，請重新 provision", 409) from exc
    if not isinstance(data, dict):
        raise ApiError(f"{label} 格式錯誤，請重新 provision", 409)
    return data


def public_key_fingerprint_from_pem(public_key_pem: str) -> str:
    try:
        public_key = load_pem_public_key(public_key_pem.encode("utf-8"))
    except Exception as exc:
        raise ApiError("Gateway public key PEM 格式無效", 409) from exc

    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        raise ApiError("Gateway identity key 必須是 EC public key", 409)
    if not isinstance(public_key.curve, ec.SECP256R1):
        raise ApiError("Gateway identity key 必須使用 SECP256R1 / P-256", 409)

    der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return sha256_bytes(der)


def expected_gateway_id(fingerprint: str) -> str:
    # 80 bits fingerprint prefix；完整 fingerprint 仍保存並驗證。
    return f"GW_{fingerprint[:20].upper()}"


def load_and_validate_identity(state_dir: Optional[Path] = None) -> Dict[str, Any]:
    private_key_path, identity_path, _ = state_paths(state_dir)
    identity = load_json_file(identity_path, "gateway_identity.json")

    required = [
        "gateway_id",
        "public_key_pem",
        "public_key_fingerprint",
        "hardware_model",
        "firmware_version",
    ]
    missing = [key for key in required if not identity.get(key)]
    if missing:
        raise ApiError(
            "Gateway identity 缺少必要欄位",
            409,
            {"missing_fields": missing},
        )

    fingerprint = public_key_fingerprint_from_pem(str(identity["public_key_pem"]))
    stored_fingerprint = str(identity["public_key_fingerprint"]).lower()
    if not hmac.compare_digest(fingerprint, stored_fingerprint):
        raise ApiError("Gateway public key fingerprint 驗證失敗", 409)

    expected_id = expected_gateway_id(fingerprint)
    if str(identity["gateway_id"]) != expected_id:
        raise ApiError(
            "Gateway ID 與 public key fingerprint 不一致",
            409,
            {"expected_gateway_id": expected_id},
        )

    if not private_key_path.exists():
        raise ApiError(f"找不到 Gateway private key：{private_key_path}", 409)
    try:
        private_key = load_pem_private_key(private_key_path.read_bytes(), password=None)
    except Exception as exc:
        raise ApiError("Gateway private key 無法讀取或格式錯誤", 409) from exc
    if not isinstance(private_key, ec.EllipticCurvePrivateKey):
        raise ApiError("Gateway identity private key 必須是 EC private key", 409)
    if not isinstance(private_key.curve, ec.SECP256R1):
        raise ApiError("Gateway identity private key 必須使用 SECP256R1 / P-256", 409)
    private_public_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_fingerprint = sha256_bytes(private_public_der)
    if not hmac.compare_digest(private_fingerprint, fingerprint):
        raise ApiError("Gateway private key 與 identity public key 不匹配", 409)

    return identity


def load_bootstrap(state_dir: Optional[Path] = None) -> Dict[str, Any]:
    _, _, bootstrap_path = state_paths(state_dir)
    return load_json_file(bootstrap_path, "gateway_bootstrap.json")


def verify_initialization_token(
    initialization_token: str,
    identity: Dict[str, Any],
    state_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    if not initialization_token:
        raise ApiError("initialization_token 為必填", 400)

    bootstrap = load_bootstrap(state_dir)
    if bootstrap.get("gateway_id") != identity.get("gateway_id"):
        raise ApiError("Gateway bootstrap 與 identity 不一致，請重新 provision", 409)

    if bool(bootstrap.get("consumed")):
        # 已成功初始化時，主流程可改走 idempotent response；此函式只處理未使用 token。
        raise ApiError(
            "Gateway initialization token 已使用",
            409,
            {"gateway_id": identity.get("gateway_id"), "token_state": "CONSUMED"},
        )

    expires_at_raw = str(bootstrap.get("token_expires_at") or "")
    expires_at = parse_iso_utc(expires_at_raw)
    if expires_at <= utc_now():
        raise ApiError(
            "Gateway initialization token 已過期，請在 Gateway 本機重新 provision/rotate token",
            410,
            {"gateway_id": identity.get("gateway_id"), "token_state": "EXPIRED"},
        )

    stored_hash = str(bootstrap.get("initialization_token_hash") or "").lower()
    supplied_hash = sha256_text(initialization_token)
    if not stored_hash or not hmac.compare_digest(stored_hash, supplied_hash):
        raise ApiError("initialization_token 驗證失敗", 403)

    return bootstrap


def bootstrap_state(state_dir: Optional[Path] = None) -> Dict[str, Any]:
    try:
        bootstrap = load_bootstrap(state_dir)
    except ApiError:
        return {"state": "NOT_PROVISIONED"}

    if bool(bootstrap.get("consumed")):
        return {
            "state": "CONSUMED",
            "consumed_at": bootstrap.get("consumed_at"),
        }

    try:
        expires_at = parse_iso_utc(str(bootstrap.get("token_expires_at") or ""))
    except ApiError:
        return {"state": "INVALID"}

    if expires_at <= utc_now():
        return {"state": "EXPIRED", "expires_at": bootstrap.get("token_expires_at")}
    return {"state": "PENDING", "expires_at": bootstrap.get("token_expires_at")}


def mark_bootstrap_consumed(
    *,
    family_id: int,
    identity: Dict[str, Any],
    state_dir: Optional[Path] = None,
) -> None:
    _, _, bootstrap_path = state_paths(state_dir)
    bootstrap = load_bootstrap(state_dir)
    if bootstrap.get("gateway_id") != identity.get("gateway_id"):
        raise ApiError("Gateway bootstrap 與 identity 不一致", 409)

    bootstrap["consumed"] = True
    bootstrap["consumed_at"] = iso_utc()
    bootstrap["bound_family_id"] = int(family_id)
    # Token hash 在成功綁定後也不必繼續保留。
    bootstrap["initialization_token_hash"] = None
    atomic_write_json(bootstrap_path, bootstrap, mode=0o600)


def require_active_user_password(
    cursor,
    user_id: str,
    password: str,
    *,
    for_update: bool = False,
) -> Dict[str, Any]:
    if not user_id or not password:
        raise ApiError("user_id、password 為必填", 400)

    sql = """
        SELECT id, user_id, username, status, password_hash
        FROM users
        WHERE user_id = %s
    """
    if for_update:
        sql += " FOR UPDATE"
    cursor.execute(sql, (user_id,))
    user = cursor.fetchone()

    if not user:
        raise ApiError("帳號或密碼錯誤", 401)
    if str(user.get("status") or "") != "Active":
        raise ApiError("此帳號已被系統停用", 403)

    password_hash = str(user.get("password_hash") or "")
    try:
        import bcrypt
    except ImportError as exc:
        raise RuntimeError("缺少 bcrypt，請先安裝專案必要套件") from exc

    try:
        valid = bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # 舊測試 DB 曾存在非 bcrypt 的假 hash；正式 UC1.3 不允許繞過密碼驗證。
        raise ApiError(
            "此帳號的 password_hash 不是有效 Bcrypt 格式，請使用 UC1.1 正式註冊帳號或修正測試資料",
            409,
            {"user_id": user_id},
        )

    if not valid:
        raise ApiError("帳號或密碼錯誤", 401)
    return user


def get_prev_hash(cursor) -> Optional[str]:
    cursor.execute(
        "SELECT current_hash FROM audit_logs ORDER BY `timestamp` DESC, command_id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    return row["current_hash"] if row and row.get("current_hash") else None


def append_audit_log(
    cursor,
    *,
    user: Dict[str, Any],
    family_id: int,
    action: str,
    parameters: Dict[str, Any],
    status: str = "Verified",
    decision: str = "ALLOW",
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    command_id = f"tx-{uuid.uuid4().hex}"
    timestamp = int(time.time())
    prev_hash = get_prev_hash(cursor)

    hash_payload = {
        "command_id": command_id,
        "user_id": user["user_id"],
        "u_id": user["id"],
        "family_id": family_id,
        "actor_type": "USER",
        "action": action,
        "parameters": parameters,
        "status": status,
        "decision": decision,
        "reason": reason,
        "timestamp": timestamp,
        "prev_hash": prev_hash,
    }
    current_hash = sha256_text(stable_json(hash_payload))

    cursor.execute(
        """
        INSERT INTO audit_logs
          (command_id, user_id, actor_type, u_id, device_id, family_id,
           action, parameters, status, decision, reason, timestamp, prev_hash, current_hash)
        VALUES
          (%s, %s, 'USER', %s, NULL, %s,
           %s, CAST(%s AS JSON), %s, %s, %s, %s, %s, %s)
        """,
        (
            command_id,
            user["user_id"],
            user["id"],
            family_id,
            action,
            json.dumps(parameters, ensure_ascii=False),
            status,
            decision,
            reason,
            timestamp,
            prev_hash,
            current_hash,
        ),
    )
    return {
        "command_id": command_id,
        "timestamp": timestamp,
        "prev_hash": prev_hash,
        "current_hash": current_hash,
    }


def build_genesis_payload(
    *,
    user_id: str,
    family_id: int,
    family_name: str,
    identity: Dict[str, Any],
) -> Dict[str, Any]:
    configuration = {
        "family_id": int(family_id),
        "family_name": family_name,
        "gateway_id": identity["gateway_id"],
        "hardware_model": identity["hardware_model"],
        "firmware_version": identity["firmware_version"],
        "public_key_fingerprint": identity["public_key_fingerprint"],
        "binding_method": BINDING_METHOD,
    }
    configuration_hash = sha256_text(stable_json(configuration))
    user_hash = sha256_text(user_id)

    return {
        "schema_version": GENESIS_SCHEMA_VERSION,
        "uc_id": UC_ID,
        "event_type": GENESIS_EVENT_TYPE,
        "family_id": int(family_id),
        "gateway_id": identity["gateway_id"],
        "source": "GATEWAY",
        "occurred_at": iso_utc(),
        "actor": {
            "actor_type": "USER",
            "actor_id_hash": f"sha256:{user_hash}",
            "actor_role": "ADMIN",
        },
        "payload": {
            "owner_binding": {
                "owner_id_hash": f"sha256:{user_hash}",
                "role": "ADMIN",
                "binding_method": BINDING_METHOD,
            },
            "gateway": {
                "gateway_id": identity["gateway_id"],
                "hardware_model": identity["hardware_model"],
                "firmware_version": identity["firmware_version"],
                # fingerprint 本身就是 public key DER 的 SHA-256。
                "public_key_hash": f"sha256:{identity['public_key_fingerprint']}",
                "initial_status": "ACTIVE",
            },
            "genesis": {
                "genesis_type": "FAMILY_SITE_GENESIS",
                "previous_block_hash": None,
                "configuration_hash": f"sha256:{configuration_hash}",
            },
        },
    }


def insert_genesis_ledger_event(
    cursor,
    *,
    user_id: str,
    family_id: int,
    identity: Dict[str, Any],
    genesis_payload: Dict[str, Any],
) -> Dict[str, Any]:
    event_id = f"LEDGER_{uuid.uuid4().hex}"
    dedup_key = f"UC1.3:SITE_GENESIS_CREATED:FAMILY:{int(family_id)}"
    payload_json = stable_json(genesis_payload)
    payload_hash = sha256_text(payload_json)

    cursor.execute(
        """
        INSERT INTO ledger_events
          (event_id, dedup_key, uc_id, event_type, family_id, gateway_id,
           created_by, payload, payload_hash, status, retry_count)
        VALUES
          (%s, %s, %s, %s, %s, %s, %s, CAST(%s AS JSON), %s, 'PENDING', 0)
        """,
        (
            event_id,
            dedup_key,
            UC_ID,
            GENESIS_EVENT_TYPE,
            family_id,
            identity["gateway_id"],
            user_id,
            payload_json,
            payload_hash,
        ),
    )
    return {
        "event_id": event_id,
        "dedup_key": dedup_key,
        "event_type": GENESIS_EVENT_TYPE,
        "status": "PENDING",
        "payload_hash": payload_hash,
    }


def fetch_gateway_initialization(cursor, gateway_id: str, *, for_update: bool = False):
    sql = """
        SELECT g.gateway_id, g.family_id, g.owner_user_id, g.gateway_name,
               g.status, g.public_key, g.public_key_fingerprint,
               g.hardware_model, g.firmware_version, g.binding_method,
               g.initialized_at, g.created_at, g.updated_at, g.last_seen_at,
               f.family_name
        FROM gateways g
        JOIN families f ON f.id = g.family_id
        WHERE g.gateway_id = %s
    """
    if for_update:
        sql += " FOR UPDATE"
    cursor.execute(sql, (gateway_id,))
    return cursor.fetchone()


def fetch_genesis_event(cursor, family_id: int) -> Optional[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT event_id, dedup_key, uc_id, event_type, family_id, gateway_id,
               payload_hash, status, ledger_reference, retry_count, last_error,
               created_at, submitted_at, confirmed_at, updated_at
        FROM ledger_events
        WHERE dedup_key = %s
        LIMIT 1
        """,
        (f"UC1.3:SITE_GENESIS_CREATED:FAMILY:{int(family_id)}",),
    )
    return cursor.fetchone()


def require_admin_access_to_gateway(cursor, user_id: str, gateway: Dict[str, Any]) -> None:
    cursor.execute(
        """
        SELECT role
        FROM user_families
        WHERE user_id = %s AND family_id = %s
        LIMIT 1
        """,
        (user_id, gateway["family_id"]),
    )
    row = cursor.fetchone()
    if not row or str(row.get("role")) != "Admin":
        raise ApiError("權限不足：只有該場域 Admin 可查詢 Gateway 初始化狀態", 403)
