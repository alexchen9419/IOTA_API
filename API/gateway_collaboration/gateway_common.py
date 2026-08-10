#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC1.4 跨場域閘道器協作共用函式。

UC1.4 的責任是建立/查詢/撤銷多個 Gateway 之間的「信任關係」。
NAT、DDNS、VPN、Port Forwarding 等實際跨網路路由不屬於本模組，
應由 UC5.6 負責。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import pymysql
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "vboxuser")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME", "devicemanagement")

# UC1.4 正式整合 UC1.3 後，只允許已完成 UC1.3 實體初始化的 Gateway 參與跨場域協作。
UC13_BINDING_METHOD = "PHYSICAL_LOCAL_CONNECTION"

try:
    PAIRING_TOKEN_TTL_SECONDS = int(os.getenv("UC1_4_PAIRING_TOKEN_TTL_SECONDS", "600"))
except ValueError:
    PAIRING_TOKEN_TTL_SECONDS = 600

# 避免設定成 0 秒或異常長時間。UC1.4 的 token 應是短效期一次性憑證。
PAIRING_TOKEN_TTL_SECONDS = max(60, min(PAIRING_TOKEN_TTL_SECONDS, 3600))

if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


class ApiError(Exception):
    """由 API 主流程捕捉並轉成 JSON 回應的可預期錯誤。"""

    def __init__(self, message: str, status_code: int = 400, data: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.data = data


def response_json(data: Dict[str, Any], status_code: int = 200) -> None:
    """輸出 CGI JSON 回應並結束程式。"""
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8")
    print("Access-Control-Allow-Origin: *")
    print("Access-Control-Allow-Methods: GET, POST, OPTIONS")
    print("Access-Control-Allow-Headers: Content-Type, Authorization\n")
    print(json.dumps(data, ensure_ascii=False, default=str))
    raise SystemExit


def get_conn():
    """建立 MySQL transaction 連線。"""
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def normalize_payload(raw_data: str) -> Dict[str, Any]:
    """接受 {payload:{...}} 或直接 {...} 兩種 JSON 格式。"""
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


def stable_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_link_id() -> str:
    return f"GTL_{uuid.uuid4().hex}"


def generate_pairing_token() -> str:
    # 32 bytes CSPRNG entropy；明文只在 create API 回傳一次。
    return f"GTPAIR_{secrets.token_urlsafe(32)}"


def utc_now_naive() -> datetime:
    """目前 DB dump 使用沒有 timezone 的 DATETIME，因此以 UTC naive datetime 寫入。"""
    return datetime.utcnow()


def token_expiry() -> datetime:
    return utc_now_naive() + timedelta(seconds=PAIRING_TOKEN_TTL_SECONDS)


def canonical_gateway_pair(gateway_1: str, gateway_2: str) -> Tuple[str, str]:
    """固定排序，避免 A-B 與 B-A 被建立為兩條重複信任。"""
    if gateway_1 == gateway_2:
        raise ApiError("兩台 Gateway 不可為同一台", 400)
    return tuple(sorted((gateway_1, gateway_2)))  # type: ignore[return-value]


def get_user(cursor, user_id: str, for_update: bool = False) -> Optional[Dict[str, Any]]:
    sql = "SELECT id, user_id, username, status FROM users WHERE user_id = %s"
    if for_update:
        sql += " FOR UPDATE"
    cursor.execute(sql, (user_id,))
    return cursor.fetchone()


def require_active_user(cursor, user_id: str) -> Dict[str, Any]:
    if not user_id:
        raise ApiError("user_id 為必填", 400)
    user = get_user(cursor, user_id)
    if not user:
        raise ApiError("找不到使用者", 404, {"user_id": user_id})
    if str(user.get("status") or "") != "Active":
        raise ApiError("使用者帳號目前不是 Active 狀態", 403, {"user_id": user_id})
    return user


def get_gateway(cursor, gateway_id: str, for_update: bool = False) -> Optional[Dict[str, Any]]:
    sql = """
        SELECT g.gateway_id, g.family_id, g.owner_user_id, g.gateway_name,
               g.status, g.public_key, g.public_key_fingerprint,
               g.hardware_model, g.firmware_version, g.binding_method, g.initialized_at,
               g.created_at, g.updated_at, g.last_seen_at,
               f.family_name
        FROM gateways g
        JOIN families f ON f.id = g.family_id
        WHERE g.gateway_id = %s
    """
    if for_update:
        sql += " FOR UPDATE"
    cursor.execute(sql, (gateway_id,))
    return cursor.fetchone()


def require_active_gateway(cursor, gateway_id: str, for_update: bool = False) -> Dict[str, Any]:
    if not gateway_id:
        raise ApiError("gateway_id 為必填", 400)
    gateway = get_gateway(cursor, gateway_id, for_update=for_update)
    if not gateway:
        raise ApiError("找不到 Gateway", 404, {"gateway_id": gateway_id})
    if str(gateway.get("status") or "") != "Active":
        raise ApiError(
            "Gateway 目前不是 Active 狀態",
            409,
            {"gateway_id": gateway_id, "gateway_status": gateway.get("status")},
        )
    return gateway


def require_collaboration_ready_gateway(
    cursor, gateway_id: str, for_update: bool = False
) -> Dict[str, Any]:
    """
    UC1.3 + UC1.4 整合後的正式協作入口。

    UC1.4 不再把「只有 gateway_id/family_id 的舊測試資料」視為可正式建立信任的 Gateway。
    只有完成 UC1.3、具備 Identity fingerprint 且為實體本地綁定的 Active Gateway 才能參與。
    """
    gateway = require_active_gateway(cursor, gateway_id, for_update=for_update)

    if gateway.get("initialized_at") is None:
        raise ApiError(
            "Gateway 尚未完成 UC1.3 初始化，無法建立跨場域信任",
            409,
            {"gateway_id": gateway_id, "required_uc": "UC1.3"},
        )

    fingerprint = str(gateway.get("public_key_fingerprint") or "").strip()
    if not fingerprint:
        raise ApiError(
            "Gateway 缺少 UC1.3 Identity fingerprint，無法建立跨場域信任",
            409,
            {"gateway_id": gateway_id, "required_uc": "UC1.3"},
        )

    if str(gateway.get("binding_method") or "") != UC13_BINDING_METHOD:
        raise ApiError(
            "Gateway 未以 UC1.3 實體本地方式完成屋主綁定",
            409,
            {
                "gateway_id": gateway_id,
                "binding_method": gateway.get("binding_method"),
                "required_binding_method": UC13_BINDING_METHOD,
            },
        )

    return gateway


def require_cross_field_gateways(gateway_a: Dict[str, Any], gateway_b: Dict[str, Any]) -> None:
    """UC1.4 定義為跨場域協作，因此兩台 Gateway 必須屬於不同 Family。"""
    if int(gateway_a["family_id"]) == int(gateway_b["family_id"]):
        raise ApiError(
            "UC1.4 僅用於不同場域 Gateway 的協作設定",
            409,
            {
                "gateway_a_id": gateway_a["gateway_id"],
                "gateway_b_id": gateway_b["gateway_id"],
                "family_id": int(gateway_a["family_id"]),
            },
        )


def get_family_role(cursor, user_id: str, family_id: int) -> Optional[str]:
    cursor.execute(
        "SELECT role FROM user_families WHERE user_id = %s AND family_id = %s",
        (user_id, family_id),
    )
    row = cursor.fetchone()
    return str(row["role"]) if row else None


def require_family_admin(cursor, user_id: str, family_id: int) -> None:
    role = get_family_role(cursor, user_id, family_id)
    if role != "Admin":
        raise ApiError(
            "權限不足：UC1.4 僅允許該場域 Admin 操作",
            403,
            {"user_id": user_id, "family_id": family_id, "current_role": role},
        )


def require_admin_on_both_gateways(
    cursor, user_id: str, gateway_a: Dict[str, Any], gateway_b: Dict[str, Any]
) -> None:
    """建立/確認跨場域信任時，操作者必須同時是兩個場域的 Admin。"""
    require_family_admin(cursor, user_id, int(gateway_a["family_id"]))
    require_family_admin(cursor, user_id, int(gateway_b["family_id"]))


def require_admin_on_either_gateway(
    cursor, user_id: str, gateway_a: Dict[str, Any], gateway_b: Dict[str, Any]
) -> int:
    """撤銷信任時任一端 Admin 都可立即切斷信任，回傳操作者所在 family_id。"""
    family_a = int(gateway_a["family_id"])
    family_b = int(gateway_b["family_id"])
    if get_family_role(cursor, user_id, family_a) == "Admin":
        return family_a
    if get_family_role(cursor, user_id, family_b) == "Admin":
        return family_b
    raise ApiError("權限不足：必須是任一關聯場域的 Admin 才能撤銷信任", 403)


def get_user_auto_id(cursor, user_id: str) -> Optional[int]:
    cursor.execute("SELECT id FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    return int(row["id"]) if row else None


def get_prev_hash(cursor) -> Optional[str]:
    cursor.execute(
        "SELECT current_hash FROM audit_logs ORDER BY `timestamp` DESC, command_id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    return row["current_hash"] if row and row.get("current_hash") else None


def append_audit_log(
    cursor,
    *,
    user_id: str,
    family_id: int,
    action: str,
    parameters: Dict[str, Any],
    status: str = "Verified",
    decision: str = "ALLOW",
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    寫入既有 audit_logs hash chain。

    UC1.4 是跨場域事件，因此呼叫端會對兩個 family 各寫一筆，
    讓 UC5.3 依 family_id 查詢時兩邊都能看到事件。
    """
    command_id = f"tx-{uuid.uuid4().hex}"
    timestamp = int(time.time())
    prev_hash = get_prev_hash(cursor)
    u_id = get_user_auto_id(cursor, user_id)

    hash_payload = {
        "command_id": command_id,
        "user_id": user_id,
        "u_id": u_id,
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
            user_id,
            u_id,
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
        "family_id": family_id,
        "timestamp": timestamp,
        "prev_hash": prev_hash,
        "current_hash": current_hash,
    }


def append_cross_family_audit_logs(
    cursor,
    *,
    user_id: str,
    gateway_a: Dict[str, Any],
    gateway_b: Dict[str, Any],
    action: str,
    parameters: Dict[str, Any],
    status: str = "Verified",
) -> list[Dict[str, Any]]:
    """同一跨場域事件在兩個場域各留下可查詢的 audit log。"""
    family_ids = []
    for family_id in (int(gateway_a["family_id"]), int(gateway_b["family_id"])):
        if family_id not in family_ids:
            family_ids.append(family_id)

    return [
        append_audit_log(
            cursor,
            user_id=user_id,
            family_id=family_id,
            action=action,
            parameters=parameters,
            status=status,
        )
        for family_id in family_ids
    ]


def expire_pending_link_if_needed(cursor, link: Dict[str, Any]) -> Dict[str, Any]:
    """若 PENDING 已超過 expires_at，將其更新為 EXPIRED。"""
    if str(link.get("status")) != "PENDING":
        return link

    expires_at = link.get("expires_at")
    if expires_at is not None and expires_at <= utc_now_naive():
        cursor.execute(
            """
            UPDATE gateway_trust_links
            SET status = 'EXPIRED', pairing_token_hash = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE link_id = %s AND status = 'PENDING'
            """,
            (link["link_id"],),
        )
        link = dict(link)
        link["status"] = "EXPIRED"
        link["pairing_token_hash"] = None
    return link


def fetch_link(cursor, link_id: str, for_update: bool = False) -> Optional[Dict[str, Any]]:
    sql = """
        SELECT link_id, gateway_a_id, gateway_b_id, created_by, status,
               pairing_token_hash, expires_at, created_at, confirmed_at,
               revoked_at, updated_at
        FROM gateway_trust_links
        WHERE link_id = %s
    """
    if for_update:
        sql += " FOR UPDATE"
    cursor.execute(sql, (link_id,))
    return cursor.fetchone()


def handle_api_error(exc: ApiError) -> None:
    body: Dict[str, Any] = {"status": "Error", "msg": exc.message}
    if exc.data is not None:
        body["data"] = exc.data
    response_json(body, exc.status_code)
