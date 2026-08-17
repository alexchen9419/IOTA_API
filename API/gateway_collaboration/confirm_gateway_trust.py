#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC1.4 - 確認跨場域 Gateway 信任。

POST JSON:
{
  "payload": {
    "user_id": "admin_001",
    "link_id": "GTL_xxx",
    "pairing_token": "GTPAIR_xxx"
  }
}
"""

import hmac
import sys

import pymysql

from gateway_common import (
    ApiError,
    append_cross_family_audit_logs,
    expire_pending_link_if_needed,
    fetch_link,
    get_conn,
    handle_api_error,
    normalize_payload,
    require_collaboration_ready_gateway,
    require_cross_field_gateways,
    require_active_user,
    require_admin_on_both_gateways,
    response_json,
    sha256_text,
)


def main() -> None:
    try:
        payload = normalize_payload(sys.stdin.read())
        user_id = str(payload.get("user_id") or "").strip()
        link_id = str(payload.get("link_id") or "").strip()
        pairing_token = str(payload.get("pairing_token") or "").strip()

        if not user_id or not link_id or not pairing_token:
            raise ApiError("user_id、link_id、pairing_token 為必填", 400)
        if not pairing_token.startswith("GTPAIR_"):
            raise ApiError("pairing_token 格式錯誤", 400)

        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                require_active_user(cursor, user_id)

                link = fetch_link(cursor, link_id, for_update=True)
                if not link:
                    raise ApiError("找不到 Gateway 信任請求", 404, {"link_id": link_id})

                link = expire_pending_link_if_needed(cursor, link)
                if link["status"] == "EXPIRED":
                    conn.commit()
                    raise ApiError("Gateway pairing_token 已過期，請重新建立信任請求", 410, {"link_id": link_id})
                if link["status"] == "ACTIVE":
                    raise ApiError("此 Gateway 信任關係已是 ACTIVE", 409, {"link_id": link_id})
                if link["status"] != "PENDING":
                    raise ApiError(
                        "此 Gateway 信任請求目前不可確認",
                        409,
                        {"link_id": link_id, "status": link["status"]},
                    )

                gateway_a = require_collaboration_ready_gateway(cursor, link["gateway_a_id"], for_update=True)
                gateway_b = require_collaboration_ready_gateway(cursor, link["gateway_b_id"], for_update=True)
                require_cross_field_gateways(gateway_a, gateway_b)
                require_admin_on_both_gateways(cursor, user_id, gateway_a, gateway_b)

                supplied_hash = sha256_text(pairing_token)
                stored_hash = str(link.get("pairing_token_hash") or "")
                if not stored_hash or not hmac.compare_digest(supplied_hash, stored_hash):
                    raise ApiError("pairing_token 驗證失敗", 403)

                cursor.execute(
                    """
                    UPDATE gateway_trust_links
                    SET status = 'ACTIVE',
                        pairing_token_hash = NULL,
                        confirmed_at = CURRENT_TIMESTAMP,
                        revoked_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE link_id = %s AND status = 'PENDING'
                    """,
                    (link_id,),
                )
                if cursor.rowcount != 1:
                    raise ApiError("信任狀態已被其他請求更新，請重新查詢", 409)

                audit_parameters = {
                    "uc": "UC1.4",
                    "link_id": link_id,
                    "gateway_a_id": link["gateway_a_id"],
                    "gateway_b_id": link["gateway_b_id"],
                    "gateway_a_family_id": gateway_a["family_id"],
                    "gateway_b_family_id": gateway_b["family_id"],
                    "trust_status": "ACTIVE",
                }
                audits = append_cross_family_audit_logs(
                    cursor,
                    user_id=user_id,
                    gateway_a=gateway_a,
                    gateway_b=gateway_b,
                    action="GATEWAY_TRUST_ESTABLISHED",
                    parameters=audit_parameters,
                    status="Success",
                )

                conn.commit()

            response_json(
                {
                    "status": "Success",
                    "msg": "UC1.4 跨場域 Gateway 信任綁定完成",
                    "data": {
                        "link_id": link_id,
                        "gateway_a_id": link["gateway_a_id"],
                        "gateway_b_id": link["gateway_b_id"],
                        "trust_status": "ACTIVE",
                        "pairing_token_consumed": True,
                        "audit": audits,
                    },
                }
            )
        except ApiError:
            # expire_pending_link_if_needed 可能更新狀態；若已 commit 則 rollback 無影響。
            conn.rollback()
            raise
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
