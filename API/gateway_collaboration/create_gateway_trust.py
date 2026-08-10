#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC1.4 - 建立跨場域 Gateway 信任請求。

POST JSON:
{
  "payload": {
    "user_id": "admin_001",
    "source_gateway_id": "GW_001",
    "target_gateway_id": "GW_002"
  }
}

成功後只回傳一次 pairing_token；資料庫只保存 SHA-256 hash。
"""

import json
import sys

import pymysql

from gateway_common import (
    ApiError,
    PAIRING_TOKEN_TTL_SECONDS,
    append_cross_family_audit_logs,
    canonical_gateway_pair,
    expire_pending_link_if_needed,
    generate_link_id,
    generate_pairing_token,
    get_conn,
    handle_api_error,
    normalize_payload,
    require_collaboration_ready_gateway,
    require_cross_field_gateways,
    require_active_user,
    require_admin_on_both_gateways,
    response_json,
    sha256_text,
    token_expiry,
)


def main() -> None:
    try:
        payload = normalize_payload(sys.stdin.read())

        user_id = str(payload.get("user_id") or "").strip()
        source_gateway_id = str(payload.get("source_gateway_id") or "").strip()
        target_gateway_id = str(payload.get("target_gateway_id") or "").strip()

        if not user_id or not source_gateway_id or not target_gateway_id:
            raise ApiError("user_id、source_gateway_id、target_gateway_id 為必填", 400)

        gateway_a_id, gateway_b_id = canonical_gateway_pair(source_gateway_id, target_gateway_id)
        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                require_active_user(cursor, user_id)

                # 鎖定 Gateway row，避免同時建立重複關係。
                gateway_a = require_collaboration_ready_gateway(cursor, gateway_a_id, for_update=True)
                gateway_b = require_collaboration_ready_gateway(cursor, gateway_b_id, for_update=True)
                require_cross_field_gateways(gateway_a, gateway_b)
                require_admin_on_both_gateways(cursor, user_id, gateway_a, gateway_b)

                # 檢查同一 pair 是否已有信任紀錄。
                cursor.execute(
                    """
                    SELECT link_id, gateway_a_id, gateway_b_id, created_by, status,
                           pairing_token_hash, expires_at, created_at, confirmed_at,
                           revoked_at, updated_at
                    FROM gateway_trust_links
                    WHERE gateway_a_id = %s AND gateway_b_id = %s
                    FOR UPDATE
                    """,
                    (gateway_a_id, gateway_b_id),
                )
                existing = cursor.fetchone()

                if existing:
                    existing = expire_pending_link_if_needed(cursor, existing)
                    if existing["status"] == "ACTIVE":
                        raise ApiError(
                            "兩台 Gateway 已建立 ACTIVE 信任關係",
                            409,
                            {"link_id": existing["link_id"], "status": existing["status"]},
                        )
                    if existing["status"] == "PENDING":
                        # 明文 token 不儲存，因此不能再次回傳；需等過期或先撤銷/重建。
                        raise ApiError(
                            "此 Gateway 組合已有尚未完成的 PENDING 綁定；原 pairing_token 不可再次取得",
                            409,
                            {
                                "link_id": existing["link_id"],
                                "status": existing["status"],
                                "expires_at": existing["expires_at"],
                            },
                        )

                link_id = generate_link_id()
                pairing_token = generate_pairing_token()
                pairing_token_hash = sha256_text(pairing_token)
                expires_at = token_expiry()

                if existing:
                    # REVOKED / EXPIRED 的相同 pair 直接重用資料列，保持 pair 唯一性。
                    cursor.execute(
                        """
                        UPDATE gateway_trust_links
                        SET link_id = %s,
                            created_by = %s,
                            status = 'PENDING',
                            pairing_token_hash = %s,
                            expires_at = %s,
                            created_at = CURRENT_TIMESTAMP,
                            confirmed_at = NULL,
                            revoked_at = NULL,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE gateway_a_id = %s AND gateway_b_id = %s
                        """,
                        (
                            link_id,
                            user_id,
                            pairing_token_hash,
                            expires_at,
                            gateway_a_id,
                            gateway_b_id,
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO gateway_trust_links
                          (link_id, gateway_a_id, gateway_b_id, created_by, status,
                           pairing_token_hash, expires_at)
                        VALUES
                          (%s, %s, %s, %s, 'PENDING', %s, %s)
                        """,
                        (
                            link_id,
                            gateway_a_id,
                            gateway_b_id,
                            user_id,
                            pairing_token_hash,
                            expires_at,
                        ),
                    )

                audit_parameters = {
                    "uc": "UC1.4",
                    "link_id": link_id,
                    "source_gateway_id": source_gateway_id,
                    "target_gateway_id": target_gateway_id,
                    "gateway_a_id": gateway_a_id,
                    "gateway_b_id": gateway_b_id,
                    "gateway_a_family_id": gateway_a["family_id"],
                    "gateway_b_family_id": gateway_b["family_id"],
                    "trust_status": "PENDING",
                    "expires_at": expires_at.isoformat(sep=" "),
                    "pairing_token_hash": pairing_token_hash,
                }
                audits = append_cross_family_audit_logs(
                    cursor,
                    user_id=user_id,
                    gateway_a=gateway_a,
                    gateway_b=gateway_b,
                    action="GATEWAY_TRUST_REQUESTED",
                    parameters=audit_parameters,
                    status="Verified",
                )

                conn.commit()

            response_json(
                {
                    "status": "Success",
                    "msg": "UC1.4 Gateway 信任請求已建立；pairing_token 僅回傳本次，請交由另一端完成確認",
                    "data": {
                        "link_id": link_id,
                        "source_gateway_id": source_gateway_id,
                        "target_gateway_id": target_gateway_id,
                        "canonical_gateway_pair": [gateway_a_id, gateway_b_id],
                        "trust_status": "PENDING",
                        "pairing_token": pairing_token,
                        "expires_at": expires_at.isoformat(sep=" "),
                        "expires_in_seconds": PAIRING_TOKEN_TTL_SECONDS,
                        "audit": audits,
                    },
                },
                201,
            )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    except ApiError as e:
        handle_api_error(e)
    except pymysql.err.IntegrityError as e:
        response_json({"status": "Error", "msg": "資料庫約束失敗", "detail": str(e)}, 409)
    except Exception as e:
        response_json({"status": "Error", "msg": "伺服器內部錯誤", "detail": str(e)}, 500)


if __name__ == "__main__":
    main()
