#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
import pymysql
import bcrypt
import os
import secrets
import uuid
import datetime as dt
from pathlib import Path
from urllib.parse import urlencode
import paho.mqtt.publish as publish
from dotenv import load_dotenv
import mqtt_tls

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
from common.audit_log_service import append_audit_log
from common.ledger_event_service import enqueue_ledger_event, hash_identifier, iso_utc, resolve_gateway_id

LOCAL_TZ = dt.timezone(dt.timedelta(hours=8))

def ledger_time(value):
    if value is None:
        return None
    parsed = value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return iso_utc(parsed.replace(tzinfo=LOCAL_TZ) if parsed.tzinfo is None else parsed)

def parse_local_time(value):
    if value in (None, ''):
        return None
    parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(LOCAL_TZ).replace(tzinfo=None)
    return parsed.replace(microsecond=0)

load_dotenv()
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_USER = os.getenv('DB_USER', 'vboxuser')
DB_PASS = os.getenv('DB_PASS', '82451258')
DB_NAME = os.getenv('DB_NAME', 'database02')
MQTT_HOST = os.getenv('MQTT_HOST', '192.168.0.84')

sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')

def response_json(data, status_code=200):
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8\n")
    print(json.dumps(data, ensure_ascii=False))
    sys.exit()

def generate_random_string(length=8):
    return ''.join(secrets.choice('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(length))

def main():
    try:
        if os.environ.get('REQUEST_METHOD', 'GET') != 'POST':
            response_json({"status": "Error", "msg": "僅支援 POST 請求方法"}, 405)

        raw_data = sys.stdin.read()
        if not raw_data:
            response_json({"status": "Error", "msg": "無輸入資料"}, 400)
        
        request_data = json.loads(raw_data)
        payload = request_data.get("payload", request_data) if isinstance(request_data, dict) else None
        if not isinstance(payload, dict):
            response_json({"status": "Error", "msg": "payload 必須是物件"}, 400)

        family_id = payload.get("family_id")
        admin_uid = payload.get("admin_uid")
        start_time = payload.get("start_time")
        end_time = payload.get("end_time")
        max_uses = payload.get("max_uses")

        if not all([family_id, admin_uid]):
            response_json({"status": "Error", "msg": "核心欄位(family_id, admin_uid)不齊全"}, 400)

        try:
            family_id = int(family_id)
            if family_id <= 0:
                raise ValueError()
            now = dt.datetime.now(LOCAL_TZ).replace(tzinfo=None, microsecond=0)
            start_time = parse_local_time(start_time) or now
            end_time = parse_local_time(end_time)
            if max_uses is not None:
                parsed_max = int(max_uses)
                if parsed_max <= 0 or isinstance(max_uses, bool) or str(parsed_max) != str(max_uses).strip():
                    raise ValueError()
                max_uses = parsed_max
            if end_time is not None and (end_time <= start_time or end_time <= now):
                raise ValueError()
            if end_time is None and max_uses is None:
                raise ValueError()
        except (TypeError, ValueError):
            response_json({"status": "Error", "msg": "family_id/時間/次數格式錯誤；期限須晚於開始與目前時間，且至少設定期限或次數"}, 400)

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
                # 1. 驗證發起者是否為該場域的 Admin
                sql_check_admin = "SELECT role FROM user_families WHERE family_id = %s AND user_id = %s FOR UPDATE"
                cursor.execute(sql_check_admin, (family_id, admin_uid))
                admin_record = cursor.fetchone()
                
                if not admin_record or admin_record['role'] != 'Admin':
                    response_json({"status": "Error", "msg": "權限拒絕：只有該場域的 Admin 才能產生訪客條碼"}, 403)

                # 2. 尋找閒置的訪客帳號 (限制條件: guest_開頭，且被撤銷或已過期)
                sql_find_idle = """
                    SELECT uf.user_id
                    FROM user_families uf JOIN users u ON u.user_id=uf.user_id
                    WHERE uf.family_id = %s AND u.status='Active'
                      AND uf.user_id LIKE 'guest_%%'
                      AND (uf.role = 'Revoked' OR (uf.end_time IS NOT NULL AND uf.end_time < NOW()))
                      AND NOT EXISTS (
                        SELECT 1 FROM user_families other
                        WHERE other.user_id=uf.user_id AND other.family_id<>uf.family_id
                          AND other.role<>'Revoked'
                      )
                    ORDER BY uf.id LIMIT 1 FOR UPDATE
                """
                cursor.execute(sql_find_idle, (family_id,))
                idle_account = cursor.fetchone()

                # 統一產生一組全新的密碼 (無論是重用還是新建，確保舊訪客無法使用)
                plain_password = secrets.token_urlsafe(24)
                hashed_password = bcrypt.hashpw(plain_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                grant_id = 'QR_' + uuid.uuid4().hex

                if idle_account:
                    # 【策略 A】：重用閒置帳號
                    guest_uid = idle_account['user_id']
                    
                    # 更新 users 表中的密碼
                    cursor.execute("UPDATE users SET password_hash = %s WHERE user_id = %s", (hashed_password, guest_uid))
                    
                    # 更新 user_families 重新賦予權限與時效
                    sql_update_family = """
                        UPDATE user_families 
                        SET role = 'Guest', status=NULL, start_time = %s, end_time = %s, max_uses = %s,
                            guest_grant_id = %s
                        WHERE user_id = %s AND family_id = %s
                    """
                    cursor.execute(sql_update_family, (start_time, end_time, max_uses, grant_id, guest_uid, family_id))
                    action_msg = f"已重用閒置訪客帳號 ({guest_uid})"
                
                else:
                    # 【策略 B】：無閒置帳號，建立全新帳號
                    guest_uid = f"guest_{secrets.token_hex(8)}"
                    dummy_email = f"{guest_uid}@temp.local"
                    dummy_phone = "0000000000"

                    sql_insert_user = """
                        INSERT INTO users (user_id, username, email, phone_number, password_hash)
                        VALUES (%s, %s, %s, %s, %s)
                    """
                    cursor.execute(sql_insert_user, (guest_uid, f"訪客_{guest_uid[-4:]}", dummy_email, dummy_phone, hashed_password))

                    sql_insert_family = """
                        INSERT INTO user_families (user_id, family_id, role, start_time, end_time, max_uses, guest_grant_id)
                        VALUES (%s, %s, 'Guest', %s, %s, %s, %s)
                    """
                    cursor.execute(sql_insert_family, (guest_uid, family_id, start_time, end_time, max_uses, grant_id))
                    action_msg = f"已建立全新訪客帳號 ({guest_uid})"

                audit = append_audit_log(
                    cursor, actor_id=admin_uid, actor_type='USER', family_id=family_id,
                    device_id=None, action='UC3.4_GUEST_TOKEN_ISSUED',
                    parameters={'authorization_id': grant_id, 'guest_user_id_hash': hash_identifier(guest_uid),
                                'start_time': ledger_time(start_time), 'end_time': ledger_time(end_time),
                                'max_uses': max_uses, 'credential_kind': 'GUEST_ACCOUNT_QR'},
                    status='Verified', decision='ALLOW', reason='GUEST_QR_AUTHORIZATION_ISSUED')
                ledger_event = enqueue_ledger_event(
                    cursor, uc_id='UC3.4', event_type='GUEST_TOKEN_ISSUED',
                    dedup_key=f'UC3.4:GUEST_TOKEN_ISSUED:GRANT:{grant_id}',
                    family_id=family_id, gateway_id=resolve_gateway_id(cursor, family_id=family_id),
                    created_by=admin_uid, source='SERVER',
                    actor={'actor_type': 'USER', 'actor_id_hash': hash_identifier(admin_uid), 'actor_role': 'ADMIN'},
                    payload={'authorization_id': grant_id, 'credential_kind': 'GUEST_ACCOUNT_QR',
                             'guest_user_id_hash': hash_identifier(guest_uid),
                             'authorization_scope': {'scope_type': 'FAMILY', 'family_id': family_id,
                                                     'role': 'GUEST', 'policy_enforced_by': 'GATEWAY'},
                             'validity': {'valid_from': ledger_time(start_time), 'expires_at': ledger_time(end_time)},
                             'usage_limit': {'max_uses': max_uses}, 'authorization_status': 'ACTIVE'})
                conn.commit()

            # 3. 實時非阻塞同步：將異動廣播至地端閘道器
            try:
                mqtt_payload = {
                    "event": "MEMBER_ROLE_CHANGED",
                    "user_data": {
                        "user_id": guest_uid,
                        "role": "Guest",
                        "start_time": str(start_time),
                        "end_time": str(end_time) if end_time else None,
                        "max_uses": max_uses
                    }
                }
                topic = f"home/security/gateway_{family_id}/auth_sync"
                
                publish.single(
                    topic=topic,
                    payload=json.dumps(mqtt_payload),
                    qos=1,
                    hostname=MQTT_HOST,
                    port=mqtt_tls.broker_port(),
                    **mqtt_tls.publish_tls_kwargs()
                )
                mqtt_msg = "權限異動已送交 MQTT broker；Gateway 套用狀態需另行確認"
                mqtt_published = True
            except Exception as mqtt_err:
                mqtt_msg = "資料庫已建立授權，但 MQTT 通知同步失敗"
                mqtt_published = False

            # 4. 回傳前端產生 QR Code 所需的 URL 與明文憑證
            base_url = os.getenv('GUEST_QR_BASE_URL', 'https://your-domain.com/qr-control')
            control_url = base_url + ('&' if '?' in base_url else '?') + urlencode({'uid': guest_uid, 'pwd': plain_password})

            response_json({
                "status": "Success", 
                "msg": f"{action_msg}。{mqtt_msg}",
                "data": {
                    "user_id": guest_uid,
                    "password": plain_password,
                    "control_url": control_url,
                    "start_time": str(start_time),
                    "end_time": str(end_time) if end_time else None,
                    "max_uses": max_uses,
                    "authorization_id": grant_id,
                    "audit_log": audit,
                    "ledger_event": ledger_event,
                    "mqtt_published": mqtt_published
                }
            })

        except pymysql.MySQLError as e:
            conn.rollback()
            response_json({"status": "Error", "msg": "資料庫操作失敗，請確認最新 schema.sql"}, 500)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    except json.JSONDecodeError:
        response_json({"status": "Error", "msg": "JSON 格式錯誤"}, 400)
    except Exception:
        response_json({"status": "Error", "msg": "伺服器內部錯誤"}, 500)

if __name__ == "__main__":
    main()
