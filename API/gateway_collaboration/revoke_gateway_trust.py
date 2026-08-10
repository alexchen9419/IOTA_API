#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC1.4 - 撤銷跨場域 Gateway 信任。

POST JSON:
{
  "payload": {
    "user_id": "admin_001",
    "link_id": "GTL_xxx",
    "reason": "不再管理此租屋處"
  }
}

任一端場域的 Admin 都可以立即撤銷，避免其中一端失陷後仍必須取得另一端同意。
"""

import sys

import pymysql

from gateway_common import (
    ApiError,
    append_cross_family_audit_logs,
    fetch_link,
    get_conn,
    handle_api_error,
    normalize_payload,
    require_active_user,
    require_admin_on_either_gateway,
    response_json,
)


def main() -> None:
    try:
        payload = normalize_payload(sys.stdin.read())
        user_id = str(payload.get("user_id") or "").strip()
        link_id = str(payload.get("link_id") or "").strip()
        reason = str(payload.get("reason") or "UC1.4 gateway trust revoked").strip()

        if not user_id or not link_id:
            raise ApiError("user_id、link_id 為必填", 400)
        if len(reason) > 255:
            raise ApiError("reason 長度不可超過 255 字元", 400)

        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                require_active_user(cursor, user_id)

                link = fetch_link(cursor, link_id, for_update=True)
                if not link:
                    raise ApiError("找不到 Gateway 信任關係", 404, {"link_id": link_id})

                # 撤銷時即使 Gateway 本身已 Disabled/Revoked，也應允許切斷信任。
                cursor.execute(
                    """
                    SELECT g.gateway_id, g.family_id, g.owner_user_id, g.gateway_name,
                           g.status, g.public_key_fingerprint, f.family_name
                    FROM gateways g
                    JOIN families f ON f.id = g.family_id
                    WHERE g.gateway_id IN (%s, %s)
                    FOR UPDATE
                    """,
                    (link["gateway_a_id"], link["gateway_b_id"]),
                )
                gateways = {row["gateway_id"]: row for row in cursor.fetchall()}
                gateway_a = gateways.get(link["gateway_a_id"])
                gateway_b = gateways.get(link["gateway_b_id"])
                if not gateway_a or not gateway_b:
                    raise ApiError("信任關係所指向的 Gateway 資料不完整", 500)

                revoked_from_family_id = require_admin_on_either_gateway(cursor, user_id, gateway_a, gateway_b)

                if link["status"] == "REVOKED":
                    response_json(
                        {
                            "status": "Success",
                            "msg": "此 Gateway 信任關係已是 REVOKED，未重複更新",
                            "data": {"link_id": link_id, "trust_status": "REVOKED"},
                        }
                    )

                previous_status = link["status"]
                cursor.execute(
                    """
                    UPDATE gateway_trust_links
                    SET status = 'REVOKED',
                        pairing_token_hash = NULL,
                        revoked_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE link_id = %s
                    """,
                    (link_id,),
                )

                audit_parameters = {
                    "uc": "UC1.4",
                    "link_id": link_id,
                    "gateway_a_id": link["gateway_a_id"],
                    "gateway_b_id": link["gateway_b_id"],
                    "gateway_a_family_id": gateway_a["family_id"],
                    "gateway_b_family_id": gateway_b["family_id"],
                    "previous_status": previous_status,
                    "trust_status": "REVOKED",
                    "revoked_from_family_id": revoked_from_family_id,
                    "reason": reason,
                }
                audits = append_cross_family_audit_logs(
                    cursor,
                    user_id=user_id,
                    gateway_a=gateway_a,
                    gateway_b=gateway_b,
                    action="GATEWAY_TRUST_REVOKED",
                    parameters=audit_parameters,
                    status="Success",
                )

                conn.commit()

            response_json(
                {
                    "status": "Success",
                    "msg": "UC1.4 Gateway 信任關係已撤銷",
                    "data": {
                        "link_id": link_id,
                        "gateway_a_id": link["gateway_a_id"],
                        "gateway_b_id": link["gateway_b_id"],
                        "previous_status": previous_status,
                        "trust_status": "REVOKED",
                        "reason": reason,
                        "audit": audits,
                    },
                }
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
