#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
import pymysql
import os
import uuid
import datetime as dt
from pathlib import Path
import paho.mqtt.publish as publish
from dotenv import load_dotenv
import mqtt_tls

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from common.audit_log_service import append_audit_log
from common.ledger_event_service import (
    enqueue_ledger_event,
    get_ledger_event_by_dedup,
    hash_identifier,
    iso_utc,
    resolve_gateway_id,
)

# 載入環境變數配置
load_dotenv()
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_USER = os.getenv('DB_USER', 'vboxuser')
DB_PASS = os.getenv('DB_PASS')
DB_NAME = os.getenv('DB_NAME', 'devicemanagement')
MQTT_HOST = os.getenv('MQTT_HOST', 'localhost')
LOCAL_TZ = dt.timezone(dt.timedelta(hours=8))

# 確保標準輸入輸出使用 UTF-8 編碼，防止中文亂碼
sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')

def response_json(data, status_code=200):
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8\n")
    print(json.dumps(data, ensure_ascii=False))
    sys.exit()


def _role_permissions(role):
    role_norm = str(role or "").strip().lower()
    if role_norm == "member":
        return ["DEVICE_VIEW", "DEVICE_CONTROL", "DASHBOARD_VIEW"]
    if role_norm == 'guest':
        return ['LIMITED_DEVICE_CONTROL']
    return []


def _revocation_dedup_key(family_id, target_uid, end_time):
    member_hash = str(hash_identifier(target_uid) or "sha256:unknown").split(":", 1)[-1][:24]
    if hasattr(end_time, "strftime"):
        stamp = end_time.strftime("%Y%m%dT%H%M%S")
    else:
        stamp = str(end_time).replace(" ", "T").replace(":", "").replace("-", "")[:20]
    return f"UC3.3:MEMBER_PERMISSION_REVOKED:FAMILY:{int(family_id)}:MEMBER:{member_hash}:AT:{stamp}"


def _ledger_local_time(value):
    return iso_utc(value.replace(tzinfo=LOCAL_TZ)) if value is not None else iso_utc()


def _publish_role_sync(family_id, target_uid, role, start_time, end_time, max_uses):
    try:
        publish.single(
            topic=f'home/security/gateway_{family_id}/auth_sync',
            payload=json.dumps({'event': 'MEMBER_ROLE_CHANGED', 'user_data': {
                'user_id': target_uid, 'role': role,
                'start_time': str(start_time) if start_time is not None else None,
                'end_time': str(end_time) if end_time is not None else None,
                'max_uses': max_uses}}, ensure_ascii=False),
            qos=1, hostname=MQTT_HOST, port=mqtt_tls.broker_port(),
            **mqtt_tls.publish_tls_kwargs())
        return '身分異動已送交 MQTT broker；Gateway 套用狀態需另行確認', True
    except Exception:
        return '雲端資料庫已完成異動，但地端 MQTT 通知同步失敗', False


def main():
    try:
        # 限制必須使用 POST 請求方法
        if os.environ.get('REQUEST_METHOD', 'GET') != 'POST':
            response_json({"status": "Error", "msg": "僅支援 POST 請求方法"}, 405)

        raw_data = sys.stdin.read()
        if not raw_data:
            response_json({"status": "Error", "msg": "無輸入資料"}, 400)
        
        request_data = json.loads(raw_data)
        payload = request_data.get("payload", request_data) if isinstance(request_data, dict) else None
        if not isinstance(payload, dict):
            response_json({"status": "Error", "msg": "payload 必須是物件"}, 400)

        # 接收核心前端參數
        family_id = payload.get("family_id")
        admin_uid = payload.get("admin_uid")    # 執行操作的屋主
        target_uid = payload.get("target_uid")   # 被操作的目標成員
        target_role = payload.get("target_role") # 預期變更的角色 (Member/Guest/Revoked等)
        start_time = payload.get("start_time")   # 臨時權限開始時間 (常駐成員請傳 null)
        end_time = payload.get("end_time")       # 臨時權限結束時間 (常駐成員請傳 null)
        max_uses = payload.get("max_uses")       # 最大使用次數限制 (無限次請傳 null)

        # 1. 基礎欄位防禦性校驗
        if not all([family_id, admin_uid, target_uid, target_role]):
            response_json({"status": "Error", "msg": "核心欄位不齊全"}, 400)
        try:
            family_id = int(family_id)
            if family_id <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            response_json({"status": "Error", "msg": "family_id 必須是正整數"}, 400)

        # 支援的角色身分組合法性檢查
        allowed_roles = ['Admin', 'Member', 'Guest', 'Technician', 'SP', 'Revoked']
        if target_role not in allowed_roles:
            response_json({"status": "Error", "msg": f"不合法的身分組類型: {target_role}"}, 400)

        # 管理員自我保護機制：不允許自己拔除自己的 Admin 權限
        if admin_uid == target_uid and target_role != 'Admin':
            response_json({"status": "Error", "msg": "Admin 無法變更或撤銷自身的權限，請保持至少一位管理員"}, 400)

        conn = pymysql.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASS,
            database=DB_NAME, charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor, autocommit=False
        )

        try:
            with conn.cursor() as cursor:
                cursor.execute("SET time_zone = '+08:00'")
                cursor.execute("SELECT id FROM families WHERE id=%s FOR UPDATE", (family_id,))
                if not cursor.fetchone():
                    response_json({"status": "Error", "msg": "找不到指定場域"}, 404)
                # 2. ⚡ 效能優化點：改用一條 SQL 同時查詢操作者與目標者在該場域的角色，節省 50% 網路 I/O
                sql_check_local = """
                    SELECT user_id, role, start_time, end_time, max_uses, guest_grant_id
                    FROM user_families 
                    WHERE family_id = %s AND user_id IN (%s, %s)
                    FOR UPDATE
                """
                cursor.execute(sql_check_local, (family_id, admin_uid, target_uid))
                local_records = cursor.fetchall()
                
                # 轉換為雜湊表實作 O(1) 複雜度極速查詢
                role_map = {row['user_id']: row['role'] for row in local_records}
                member_map = {row['user_id']: row for row in local_records}
                target_record = member_map.get(target_uid) or {}

                # 權限核心查驗：確認操作者是不是該家庭的 Admin
                if role_map.get(admin_uid) != 'Admin':
                    response_json({"status": "Error", "msg": "權限拒絕：只有該場域的 Admin 才能管理成員身分"}, 403)

                previous_role = role_map.get(target_uid)
                if target_role == 'Revoked' and previous_role is None:
                    response_json({"status": "Error", "msg": "目標使用者不是此場域成員，無可撤銷權限"}, 404)
                if target_role == 'Revoked' and previous_role == 'Admin':
                    cursor.execute(
                        "SELECT COUNT(*) AS cnt FROM user_families WHERE family_id=%s AND role='Admin'",
                        (family_id,),
                    )
                    admin_count = int((cursor.fetchone() or {}).get('cnt') or 0)
                    if admin_count <= 1:
                        response_json({"status": "Error", "msg": "不可撤銷場域中唯一的 Admin，請先完成管理權移轉"}, 409)

                # 3. 🔍 全域資安校驗：檢查目標用戶是否「存在於全域使用者表」且「帳號狀態正常」
                sql_check_global = "SELECT status FROM users WHERE user_id = %s"
                cursor.execute(sql_check_global, (target_uid,))
                global_user = cursor.fetchone()
                
                if not global_user:
                    response_json({"status": "Error", "msg": "授權失敗：該目標帳號尚未在平台註冊"}, 404)
                
                if global_user['status'] != 'Active':
                    response_json({"status": "Error", "msg": "授權失敗：該目標帳號已被系統全域停用"}, 403)

                # 冪等性：已撤銷時回傳既有 Ledger Event，不再建立第二筆撤銷事件。
                if previous_role == target_role and target_role == 'Revoked':
                    cursor.execute(
                        "SELECT end_time FROM user_families WHERE family_id=%s AND user_id=%s LIMIT 1",
                        (family_id, target_uid),
                    )
                    existing_row = cursor.fetchone() or {}
                    grant_id = target_record.get('guest_grant_id')
                    dedup_key = (f'UC3.5:GUEST_TOKEN_REVOKED:GRANT:{grant_id}' if grant_id else
                                 _revocation_dedup_key(family_id, target_uid, existing_row.get('end_time')))
                    ledger_event = get_ledger_event_by_dedup(cursor, dedup_key)
                    conn.commit()
                    mqtt_msg, mqtt_published = _publish_role_sync(
                        family_id, target_uid, 'Revoked', target_record.get('start_time'),
                        existing_row.get('end_time'), target_record.get('max_uses'))
                    response_json({
                        "status": "Success",
                        "msg": f"該使用者權限先前已被撤銷，未重複建立 Ledger Event。{mqtt_msg}",
                        "data": {"ledger_event": ledger_event, "mqtt_published": mqtt_published},
                    }, 200)

                ledger_event = None

                # 4. 🦾 核心 Upsert 業務分流（實現免邀請直接授權）
                if target_role == 'Revoked':
                    is_guest = str(previous_role or '').lower() == 'guest'
                    grant_id = (target_record.get('guest_grant_id') or 'LEGACY_' + uuid.uuid4().hex) if is_guest else None
                    # 【撤銷權限】強制將角色改為 Revoked，並將結束時間強制歸零設定為系統當前時間 (NOW)
                    upsert_sql = """
                        INSERT INTO user_families (user_id, family_id, role, end_time, guest_grant_id)
                        VALUES (%s, %s, 'Revoked', NOW(), %s)
                        ON DUPLICATE KEY UPDATE role = 'Revoked', end_time = NOW(),
                                                guest_grant_id=VALUES(guest_grant_id)
                    """
                    cursor.execute(upsert_sql, (target_uid, family_id, grant_id))
                    cursor.execute(
                        "SELECT end_time FROM user_families WHERE family_id=%s AND user_id=%s LIMIT 1",
                        (family_id, target_uid),
                    )
                    revoked_row = cursor.fetchone() or {}
                    revoked_at_db = revoked_row.get('end_time')
                    revoked_at = _ledger_local_time(revoked_at_db)
                    gateway_id = resolve_gateway_id(cursor, family_id=int(family_id))
                    dedup_key = (f'UC3.5:GUEST_TOKEN_REVOKED:GRANT:{grant_id}' if is_guest else
                                 _revocation_dedup_key(family_id, target_uid, revoked_at_db or revoked_at))
                    uc_id = 'UC3.5' if is_guest else 'UC3.3'
                    event_type = 'GUEST_TOKEN_REVOKED' if is_guest else 'MEMBER_PERMISSION_REVOKED'
                    reason_code = str(payload.get('reason_code') or (
                        'ADMIN_MANUAL_REVOCATION' if is_guest else 'ADMIN_REMOVED_MEMBER')).strip().upper()[:64]

                    audit = append_audit_log(
                        cursor,
                        actor_id=admin_uid,
                        actor_type="USER",
                        family_id=int(family_id),
                        device_id=None,
                        action=f'{uc_id}_{event_type}',
                        parameters={
                            "target_uid": target_uid,
                            "previous_role": previous_role,
                            "new_role": "Revoked",
                            "permission_invalid_at": revoked_at,
                            "authorization_id": grant_id,
                            "reason_code": reason_code,
                            "reason_detail_hash": hash_identifier(payload.get('reason')),
                        },
                        status="Verified",
                        decision="ALLOW",
                        reason=reason_code,
                    )
                    ledger_event = enqueue_ledger_event(
                        cursor,
                        uc_id=uc_id,
                        event_type=event_type,
                        dedup_key=dedup_key,
                        family_id=int(family_id),
                        gateway_id=gateway_id,
                        created_by=admin_uid,
                        source="SERVER",
                        timestamp=revoked_at,
                        actor={
                            "actor_type": "USER",
                            "actor_id_hash": hash_identifier(admin_uid),
                            "actor_role": "ADMIN",
                        },
                        payload={
                            "member_id_hash": hash_identifier(target_uid),
                            "scope": {
                                "scope_type": "FAMILY",
                                "family_id": int(family_id),
                                "affects_other_families": False,
                            },
                            "role_revocation": {
                                "previous_role": str(previous_role or "UNKNOWN").upper(),
                                "new_role": None,
                                "revoked_permissions": _role_permissions(previous_role),
                            },
                            "permission_invalid_at": revoked_at,
                            "reason_code": reason_code,
                            "operated_by_hash": hash_identifier(admin_uid),
                            **({'authorization_id': grant_id,
                                'credential_kind': ('GUEST_ACCOUNT_QR' if str(grant_id).startswith('QR_') else
                                                    'GUEST_ACCOUNT_ROLE' if str(grant_id).startswith('ROLE_') else
                                                    'LEGACY_GUEST_ACCOUNT'),
                                'guest_user_id_hash': hash_identifier(target_uid),
                                'previous_status': 'GUEST', 'new_status': 'REVOKED',
                                'revoked_at': revoked_at, 'remaining_uses_at_revocation': None,
                                'revocation_reason': {'reason_code': reason_code,
                                    'reason_detail_hash': hash_identifier(payload.get('reason'))}}
                               if is_guest else {}),
                        },
                    )
                    action_msg = "已成功撤銷該使用者所有權限 (變更為 Revoked)"
                    # Send the stored revocation time, not an optional/null client value.
                    start_time = target_record.get('start_time')
                    end_time = revoked_at_db
                    max_uses = target_record.get('max_uses')
                else:
                    # A Guest role grant/renewal is a separate authorization lifecycle.
                    # Retaining a revoked grant ID here would suppress its next revocation.
                    grant_id = 'ROLE_' + uuid.uuid4().hex if target_role == 'Guest' else None
                    # 【發行/調整身分/恢復權限】如果原先查無紀錄直接 INSERT，有舊紀錄則直接覆蓋洗白
                    upsert_sql = """
                        INSERT INTO user_families (user_id, family_id, role, start_time, end_time, max_uses, guest_grant_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE 
                            role = VALUES(role), 
                            start_time = VALUES(start_time), 
                            end_time = VALUES(end_time),
                            max_uses = VALUES(max_uses),
                            guest_grant_id = VALUES(guest_grant_id)
                    """
                    cursor.execute(upsert_sql, (target_uid, family_id, target_role, start_time, end_time, max_uses, grant_id))
                    if target_role == 'Guest':
                        def to_ledger(value):
                            if value in (None, ''):
                                return None
                            parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
                            return iso_utc(parsed.replace(tzinfo=LOCAL_TZ) if parsed.tzinfo is None else parsed)
                        audit = append_audit_log(
                            cursor, actor_id=admin_uid, actor_type='USER', family_id=family_id,
                            device_id=None, action='UC3.4_GUEST_TOKEN_ISSUED',
                            parameters={'authorization_id': grant_id, 'target_uid': target_uid,
                                        'credential_kind': 'GUEST_ACCOUNT_ROLE',
                                        'start_time': start_time, 'end_time': end_time, 'max_uses': max_uses},
                            reason='ADMIN_GRANTED_GUEST_ROLE')
                        ledger_event = enqueue_ledger_event(
                            cursor, uc_id='UC3.4', event_type='GUEST_TOKEN_ISSUED',
                            dedup_key=f'UC3.4:GUEST_TOKEN_ISSUED:GRANT:{grant_id}',
                            family_id=family_id, gateway_id=resolve_gateway_id(cursor, family_id=family_id),
                            created_by=admin_uid, source='SERVER',
                            actor={'actor_type': 'USER', 'actor_id_hash': hash_identifier(admin_uid), 'actor_role': 'ADMIN'},
                            payload={'authorization_id': grant_id, 'credential_kind': 'GUEST_ACCOUNT_ROLE',
                                     'guest_user_id_hash': hash_identifier(target_uid),
                                     'authorization_scope': {'scope_type': 'FAMILY', 'family_id': family_id, 'role': 'GUEST',
                                                             'policy_enforced_by': 'GATEWAY'},
                                     'validity': {'valid_from': to_ledger(start_time), 'expires_at': to_ledger(end_time)},
                                     'usage_limit': {'max_uses': max_uses}, 'authorization_status': 'ACTIVE'})
                    action_msg = f"已成功將該使用者身分更新為 {target_role}"

                conn.commit()

            # 5. 📡 實時非阻塞同步：利用 single() 將異動動態廣播至地端閘道器
            mqtt_msg, mqtt_published = _publish_role_sync(
                family_id, target_uid, target_role, start_time, end_time, max_uses)

            response = {
                "status": "Success",
                "msg": f"{action_msg}。{mqtt_msg}",
            }
            response['data'] = {'mqtt_published': mqtt_published}
            if ledger_event is not None:
                response["data"].update({"ledger_event": ledger_event, "audit_log": audit})
            response_json(response)

        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    except json.JSONDecodeError:
        response_json({"status": "Error", "msg": "JSON 格式錯誤"}, 400)
    except Exception:
        response_json({"status": "Error", "msg": "伺服器內部錯誤，請確認最新 schema.sql"}, 500)

if __name__ == "__main__":
    main()
