#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC1.3 - 閘道器初始化與屋主綁定。

POST JSON:
{
  "payload": {
    "user_id": "uc13_admin",
    "password": "Pass12345",
    "family_name": "台北住家",
    "gateway_name": "台北住家 Gateway",
    "initialization_token": "GWINIT_..."
  }
}

Gateway identity 不由 App 傳入，而是從本機 gateway_runtime/gateway_identity.json 讀取。
"""

from __future__ import annotations

import json
import sys

from gateway_init_common import (
    ApiError,
    BINDING_METHOD,
    append_audit_log,
    build_genesis_payload,
    fetch_gateway_initialization,
    fetch_genesis_event,
    get_conn,
    handle_api_error,
    insert_genesis_ledger_event,
    load_and_validate_identity,
    load_bootstrap,
    mark_bootstrap_consumed,
    normalize_payload,
    public_key_fingerprint_from_pem,
    require_active_user_password,
    response_json,
    verify_initialization_token,
)


def validate_name(value: str, field: str, max_length: int = 100) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ApiError(f"{field} 為必填", 400)
    if len(cleaned) > max_length:
        raise ApiError(f"{field} 長度不可超過 {max_length} 字元", 400)
    return cleaned


def build_existing_response(cursor, gateway: dict) -> dict:
    event = fetch_genesis_event(cursor, int(gateway["family_id"]))
    if not event:
        raise ApiError(
            "Gateway 顯示已初始化，但缺少 SITE_GENESIS_CREATED ledger event；資料狀態不一致",
            500,
            {"gateway_id": gateway["gateway_id"], "family_id": gateway["family_id"]},
        )
    return {
        "already_initialized": True,
        "family_id": int(gateway["family_id"]),
        "family_name": gateway["family_name"],
        "gateway_id": gateway["gateway_id"],
        "gateway_name": gateway["gateway_name"],
        "gateway_status": gateway["status"],
        "owner_user_id": gateway["owner_user_id"],
        "public_key_fingerprint": gateway["public_key_fingerprint"],
        "initialized_at": gateway["initialized_at"],
        "genesis_event": {
            "event_id": event["event_id"],
            "event_type": event["event_type"],
            "ledger_status": event["status"],
            "payload_hash": event["payload_hash"],
            "ledger_reference": event.get("ledger_reference"),
        },
    }


def main() -> None:
    conn = None
    identity = None
    family_id_for_local_state = None
    local_state_warning = None

    try:
        if str(__import__("os").environ.get("REQUEST_METHOD", "")).upper() == "OPTIONS":
            response_json({"status": "Success"}, 200)

        payload = normalize_payload(sys.stdin.read())
        user_id = str(payload.get("user_id") or "").strip()
        password = str(payload.get("password") or "")
        family_name = validate_name(str(payload.get("family_name") or ""), "family_name")
        gateway_name = validate_name(str(payload.get("gateway_name") or ""), "gateway_name")
        initialization_token = str(payload.get("initialization_token") or "").strip()

        if not user_id or not password or not initialization_token:
            raise ApiError("user_id、password、initialization_token 為必填", 400)

        identity = load_and_validate_identity()
        gateway_id = str(identity["gateway_id"])
        identity_fingerprint = str(identity["public_key_fingerprint"]).lower()

        conn = get_conn()
        with conn.cursor() as cursor:
            # 身分驗證先做，避免僅靠 user_id 進行敏感初始化。
            user = require_active_user_password(
                cursor, user_id, password, for_update=True
            )

            existing = fetch_gateway_initialization(cursor, gateway_id, for_update=True)
            if existing and existing.get("initialized_at") is not None:
                if str(existing.get("owner_user_id")) != user_id:
                    raise ApiError("Gateway 已綁定其他屋主", 409, {"gateway_id": gateway_id})
                db_fingerprint = str(existing.get("public_key_fingerprint") or "").lower()
                if not db_fingerprint or db_fingerprint != identity_fingerprint:
                    raise ApiError("Gateway identity 與資料庫已綁定公鑰不一致", 409)

                # 即使本機 bootstrap 因前次 DB commit 後寫檔失敗仍為 PENDING，
                # 相同 owner 的重送請求應回傳同一結果並補標記 consumed。
                result = build_existing_response(cursor, existing)
                family_id_for_local_state = int(existing["family_id"])
                conn.commit()
                try:
                    mark_bootstrap_consumed(
                        family_id=family_id_for_local_state, identity=identity
                    )
                except Exception as exc:
                    local_state_warning = f"DB 已初始化，但本機 bootstrap consumed 狀態更新失敗：{exc}"
                if local_state_warning:
                    result["local_state_warning"] = local_state_warning
                response_json(
                    {
                        "status": "Success",
                        "msg": "Gateway 已完成初始化",
                        "data": result,
                    },
                    200,
                )

            # 尚未初始化時，必須驗證物理持有的一次性 initialization token。
            verify_initialization_token(initialization_token, identity)

            # 防止同一 public key 被換 Gateway ID 重複註冊。
            cursor.execute(
                """
                SELECT gateway_id, family_id, owner_user_id, initialized_at
                FROM gateways
                WHERE public_key_fingerprint = %s AND gateway_id <> %s
                LIMIT 1
                FOR UPDATE
                """,
                (identity_fingerprint, gateway_id),
            )
            duplicate_fingerprint = cursor.fetchone()
            if duplicate_fingerprint:
                raise ApiError(
                    "此 Gateway public key fingerprint 已綁定其他 Gateway ID",
                    409,
                    {
                        "existing_gateway_id": duplicate_fingerprint["gateway_id"],
                        "requested_gateway_id": gateway_id,
                    },
                )

            created_new_family = False
            if existing:
                # 相容 UC1.4 舊測試/匯入資料：若同 gateway_id 已存在但尚未完成 UC1.3，
                # 僅允許原 owner 使用同一場域完成正式初始化，不另建 Family。
                if str(existing.get("owner_user_id")) != user_id:
                    raise ApiError("Gateway ID 已被其他屋主占用", 409)
                db_fingerprint = str(existing.get("public_key_fingerprint") or "").lower()
                if db_fingerprint and db_fingerprint != identity_fingerprint:
                    raise ApiError("既有 Gateway public key 與本機 identity 不一致", 409)

                family_id = int(existing["family_id"])
                cursor.execute(
                    "SELECT id, family_name, admin_uid FROM families WHERE id = %s FOR UPDATE",
                    (family_id,),
                )
                family = cursor.fetchone()
                if not family:
                    raise ApiError("既有 Gateway 指向不存在的場域", 500)
                if str(family.get("admin_uid")) != user_id:
                    raise ApiError("既有場域 Admin 與 Gateway owner 不一致", 409)

                cursor.execute(
                    """
                    INSERT INTO user_families (user_id, family_id, role)
                    VALUES (%s, %s, 'Admin')
                    ON DUPLICATE KEY UPDATE role = 'Admin'
                    """,
                    (user_id, family_id),
                )
                family_name_effective = str(family["family_name"])

                cursor.execute(
                    """
                    UPDATE gateways
                    SET gateway_name = %s,
                        status = 'Active',
                        public_key = %s,
                        public_key_fingerprint = %s,
                        hardware_model = %s,
                        firmware_version = %s,
                        binding_method = %s,
                        initialized_at = UTC_TIMESTAMP(),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE gateway_id = %s
                    """,
                    (
                        gateway_name,
                        identity["public_key_pem"],
                        identity_fingerprint,
                        identity["hardware_model"],
                        identity["firmware_version"],
                        BINDING_METHOD,
                        gateway_id,
                    ),
                )
            else:
                cursor.execute(
                    "INSERT INTO families (family_name, admin_uid) VALUES (%s, %s)",
                    (family_name, user_id),
                )
                family_id = int(cursor.lastrowid)
                family_name_effective = family_name
                created_new_family = True

                cursor.execute(
                    """
                    INSERT INTO user_families (user_id, family_id, role)
                    VALUES (%s, %s, 'Admin')
                    """,
                    (user_id, family_id),
                )

                # 再計算一次 fingerprint，確保寫入 DB 的公鑰內容與已驗證 identity 一致。
                verified_fingerprint = public_key_fingerprint_from_pem(
                    str(identity["public_key_pem"])
                )
                if verified_fingerprint != identity_fingerprint:
                    raise ApiError("Gateway public key fingerprint 驗證失敗", 409)

                cursor.execute(
                    """
                    INSERT INTO gateways
                      (gateway_id, family_id, owner_user_id, gateway_name, status,
                       public_key, public_key_fingerprint, hardware_model,
                       firmware_version, binding_method, initialized_at)
                    VALUES
                      (%s, %s, %s, %s, 'Active',
                       %s, %s, %s, %s, %s, UTC_TIMESTAMP())
                    """,
                    (
                        gateway_id,
                        family_id,
                        user_id,
                        gateway_name,
                        identity["public_key_pem"],
                        identity_fingerprint,
                        identity["hardware_model"],
                        identity["firmware_version"],
                        BINDING_METHOD,
                    ),
                )

            audit_parameters = {
                "family_id": family_id,
                "gateway_id": gateway_id,
                "owner_user_id": user_id,
                "gateway_name": gateway_name,
                "binding_method": BINDING_METHOD,
                "public_key_fingerprint": identity_fingerprint,
                "hardware_model": identity["hardware_model"],
                "firmware_version": identity["firmware_version"],
                "created_new_family": created_new_family,
            }
            audit = append_audit_log(
                cursor,
                user=user,
                family_id=family_id,
                action="GATEWAY_INITIALIZED",
                parameters=audit_parameters,
                status="Verified",
            )

            genesis_payload = build_genesis_payload(
                user_id=user_id,
                family_id=family_id,
                family_name=family_name_effective,
                identity=identity,
            )
            ledger_event = insert_genesis_ledger_event(
                cursor,
                user_id=user_id,
                family_id=family_id,
                identity=identity,
                genesis_payload=genesis_payload,
            )

            conn.commit()
            family_id_for_local_state = family_id

            # 本機 token consumed 標記必須在 DB commit 後做：若 DB rollback，不可先燒掉 token。
            try:
                mark_bootstrap_consumed(family_id=family_id, identity=identity)
            except Exception as exc:
                local_state_warning = (
                    "資料庫初始化已成功，但本機 bootstrap consumed 狀態更新失敗；"
                    f"重送相同初始化請求可安全修復：{exc}"
                )

            data = {
                "already_initialized": False,
                "family_id": family_id,
                "family_name": family_name_effective,
                "created_new_family": created_new_family,
                "owner": {"user_id": user_id, "role": "Admin"},
                "gateway": {
                    "gateway_id": gateway_id,
                    "gateway_name": gateway_name,
                    "status": "Active",
                    "hardware_model": identity["hardware_model"],
                    "firmware_version": identity["firmware_version"],
                    "curve": identity.get("curve", "SECP256R1"),
                    "public_key_fingerprint": identity_fingerprint,
                    "binding_method": BINDING_METHOD,
                },
                "audit_log": {
                    "command_id": audit["command_id"],
                    "action": "GATEWAY_INITIALIZED",
                    "current_hash": audit["current_hash"],
                },
                "genesis_event": {
                    **ledger_event,
                    "note": "目前僅建立待上鏈事件；未接入 IOTA Ledger Worker 前維持 PENDING",
                },
            }
            if local_state_warning:
                data["local_state_warning"] = local_state_warning

            response_json(
                {
                    "status": "Success",
                    "msg": "UC1.3 Gateway 初始化、屋主綁定與 Genesis 待上鏈事件建立完成",
                    "data": data,
                },
                201,
            )

    except ApiError as exc:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        handle_api_error(exc)
    except Exception as exc:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        response_json(
            {
                "status": "Error",
                "msg": "伺服器內部錯誤",
                "detail": str(exc),
            },
            500,
        )
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
