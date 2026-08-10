#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC1.4 - 查詢目前使用者可管理的跨場域 Gateway 信任關係。

POST JSON:
{
  "payload": {
    "user_id": "admin_001",
    "gateway_id": "GW_001",      // optional
    "family_id": 12,              // optional
    "status": "ACTIVE"           // optional: PENDING/ACTIVE/REVOKED/EXPIRED
  }
}

為避免暴露 Gateway 拓樸，僅回傳 user_id 在其 family 中角色為 Admin 的關係。
"""

import sys

import pymysql

from gateway_common import (
    ApiError,
    get_conn,
    handle_api_error,
    normalize_payload,
    require_active_user,
    response_json,
)

VALID_STATUSES = {"PENDING", "ACTIVE", "REVOKED", "EXPIRED"}


def main() -> None:
    try:
        payload = normalize_payload(sys.stdin.read())
        user_id = str(payload.get("user_id") or "").strip()
        gateway_id = str(payload.get("gateway_id") or "").strip() or None
        family_id = payload.get("family_id")
        status_filter = str(payload.get("status") or "").strip().upper() or None

        if not user_id:
            raise ApiError("user_id 為必填", 400)
        if status_filter and status_filter not in VALID_STATUSES:
            raise ApiError("status 僅允許 PENDING、ACTIVE、REVOKED、EXPIRED", 400)
        if family_id is not None:
            try:
                family_id = int(family_id)
            except (TypeError, ValueError):
                raise ApiError("family_id 必須是整數", 400)

        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                require_active_user(cursor, user_id)

                # 查詢前先將已過期的 PENDING 轉為 EXPIRED。
                cursor.execute(
                    """
                    UPDATE gateway_trust_links
                    SET status = 'EXPIRED', pairing_token_hash = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE status = 'PENDING' AND expires_at IS NOT NULL AND expires_at <= UTC_TIMESTAMP()
                    """
                )

                conditions = [
                    "(ufa.role = 'Admin' OR ufb.role = 'Admin')",
                    "(ufa.user_id = %s OR ufb.user_id = %s)",
                ]
                params = [user_id, user_id]

                if gateway_id:
                    conditions.append("(l.gateway_a_id = %s OR l.gateway_b_id = %s)")
                    params.extend([gateway_id, gateway_id])
                if family_id is not None:
                    conditions.append("(ga.family_id = %s OR gb.family_id = %s)")
                    params.extend([family_id, family_id])
                if status_filter:
                    conditions.append("l.status = %s")
                    params.append(status_filter)

                sql = f"""
                    SELECT
                        l.link_id,
                        l.status AS trust_status,
                        l.created_by,
                        l.expires_at,
                        l.created_at,
                        l.confirmed_at,
                        l.revoked_at,
                        l.updated_at,
                        ga.gateway_id AS gateway_a_id,
                        ga.gateway_name AS gateway_a_name,
                        ga.status AS gateway_a_status,
                        ga.initialized_at AS gateway_a_initialized_at,
                        ga.binding_method AS gateway_a_binding_method,
                        ga.family_id AS gateway_a_family_id,
                        fa.family_name AS gateway_a_family_name,
                        gb.gateway_id AS gateway_b_id,
                        gb.gateway_name AS gateway_b_name,
                        gb.status AS gateway_b_status,
                        gb.initialized_at AS gateway_b_initialized_at,
                        gb.binding_method AS gateway_b_binding_method,
                        gb.family_id AS gateway_b_family_id,
                        fb.family_name AS gateway_b_family_name
                    FROM gateway_trust_links l
                    JOIN gateways ga ON ga.gateway_id = l.gateway_a_id
                    JOIN gateways gb ON gb.gateway_id = l.gateway_b_id
                    JOIN families fa ON fa.id = ga.family_id
                    JOIN families fb ON fb.id = gb.family_id
                    LEFT JOIN user_families ufa
                      ON ufa.user_id = %s AND ufa.family_id = ga.family_id
                    LEFT JOIN user_families ufb
                      ON ufb.user_id = %s AND ufb.family_id = gb.family_id
                    WHERE {' AND '.join(conditions)}
                    ORDER BY l.updated_at DESC, l.link_id DESC
                """
                # LEFT JOIN 也需要 user_id 參數，因此前面再加兩個。
                cursor.execute(sql, [user_id, user_id] + params)
                rows = cursor.fetchall()
                conn.commit()

            response_json(
                {
                    "status": "Success",
                    "msg": "UC1.4 Gateway 信任關係查詢完成",
                    "data": {
                        "user_id": user_id,
                        "count": len(rows),
                        "filters": {
                            "gateway_id": gateway_id,
                            "family_id": family_id,
                            "status": status_filter,
                        },
                        "trust_links": rows,
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
    except Exception as e:
        response_json({"status": "Error", "msg": "伺服器內部錯誤", "detail": str(e)}, 500)


if __name__ == "__main__":
    main()
