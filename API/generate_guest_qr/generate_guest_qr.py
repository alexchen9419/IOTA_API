#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UC3.4 產生訪客 QR → UC4.2 訪客掃碼開門。

舊版是建立/重用一組 guest_ 訪客帳號、把明文密碼塞進 QR 網址的 query string，
但 control_device.py 的帳號驗證只放行 Admin/Member，訪客帳號根本開不了門；
密碼放 query string 也會留在瀏覽器歷史、伺服器 access log 與 Referer 裡。

現在改發 guest_tokens（control_device.py 的 auth_type=guest_token 本來就支援
期限、次數、撤銷、限定動作與裝置），資料庫只存 SHA-256。QR 網址指向閘道提供
的訪客頁 /guest，token 放在 URL fragment（#t=...）——fragment 不會隨 HTTP
請求送出，不會進 access log 或 Referer，只有訪客手機上的頁面讀得到。
"""

import json
import sys
import os
import hashlib
import secrets
import datetime as dt
from pathlib import Path
import pymysql
from dotenv import load_dotenv

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
from common.audit_log_service import append_audit_log
from common.ledger_event_service import enqueue_ledger_event, hash_identifier, iso_utc, resolve_gateway_id
from common.guest_token_limits import UNLIMITED_USES

LOCAL_TZ = dt.timezone(dt.timedelta(hours=8))
# 只給次數、沒給期限時的預設有效時間（guest_tokens.expires_at 不可為 NULL）
DEFAULT_VALID_HOURS = int(os.getenv('GUEST_QR_DEFAULT_HOURS', '24'))
GUEST_ACTIONS = ['UNLOCK']

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

def local_to_utc_naive(value):
    """guest_tokens 的時間跟 control_device.py 的 now_utc() 比對，存 UTC。"""
    return value.replace(tzinfo=LOCAL_TZ).astimezone(dt.timezone.utc).replace(tzinfo=None)

def guest_page_base_url():
    """訪客頁網址。正式環境用 GUEST_QR_BASE_URL 指定對外網址（建議 HTTPS）；
    沒設就用 App 打進來的那個 host（gateway.py 會帶 HTTP_HOST），
    App 連得到的位址，同一個區網的訪客手機也連得到。"""
    base = os.getenv('GUEST_QR_BASE_URL', '').strip()
    if base:
        return base.rstrip('#')
    host = os.environ.get('HTTP_HOST', '').strip()
    if not host:
        return None
    scheme = os.environ.get('REQUEST_SCHEME', 'http')
    return f"{scheme}://{host}/guest"

load_dotenv()
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_USER = os.getenv('DB_USER', 'vboxuser')
DB_PASS = (os.getenv('DB_PASS') or os.environ['DB_PASSWORD'])
DB_NAME = os.getenv('DB_NAME', 'database02')

sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')

def response_json(data, status_code=200):
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8\n")
    print(json.dumps(data, ensure_ascii=False))
    sys.exit()

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
        device_id = str(payload.get("device_id") or '').strip() or None
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

        effective_end = end_time or (start_time + dt.timedelta(hours=DEFAULT_VALID_HOURS))
        stored_max_uses = max_uses if max_uses is not None else UNLIMITED_USES

        base_url = guest_page_base_url()
        if not base_url:
            response_json({"status": "Error", "msg": "無法決定訪客頁網址，請設定 GUEST_QR_BASE_URL"}, 500)

        conn = pymysql.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASS,
            database=DB_NAME, charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor, autocommit=False
        )

        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id FROM families WHERE id=%s FOR UPDATE", (family_id,))
                if not cursor.fetchone():
                    response_json({"status": "Error", "msg": "找不到指定場域"}, 404)
                # 1. 驗證發起者是否為該場域的 Admin
                cursor.execute(
                    "SELECT role FROM user_families WHERE family_id = %s AND user_id = %s FOR UPDATE",
                    (family_id, admin_uid))
                admin_record = cursor.fetchone()
                if not admin_record or admin_record['role'] != 'Admin':
                    response_json({"status": "Error", "msg": "權限拒絕：只有該場域的 Admin 才能產生訪客條碼"}, 403)

                # 2. 指定裝置時確認屬於此場域；不指定 = 場域內所有裝置（device_id NULL）
                gateway_hint = None
                if device_id:
                    cursor.execute("SELECT family_id, gateway_id, status FROM devices WHERE device_id=%s",
                                   (device_id,))
                    device = cursor.fetchone()
                    if not device or device.get('family_id') is None or int(device['family_id']) != family_id:
                        response_json({"status": "Error", "msg": "找不到此場域的指定裝置"}, 404)
                    gateway_hint = device.get('gateway_id')

                # 3. 舊 DB 沒有 valid_from 欄位時，無法表達「未來才生效」
                cursor.execute("SHOW COLUMNS FROM guest_tokens LIKE 'valid_from'")
                has_valid_from = cursor.fetchone() is not None
                if start_time > now and not has_valid_from:
                    response_json({"status": "Error",
                                   "msg": "資料庫缺少 guest_tokens.valid_from 欄位，無法設定未來開始時間；請依 WHITEPAPER 第 4 節補欄位"}, 500)

                # 4. 核發 token：明文只出現在這次回應，DB 只存 SHA-256
                token_plain = f"GUEST_{secrets.token_urlsafe(32)}"
                token_hash = hashlib.sha256(token_plain.encode('utf-8')).hexdigest()
                token_id = f"GT_{dt.datetime.utcnow().strftime('%Y%m%d')}_{secrets.token_hex(8)}"
                columns = ['token_id', 'token_hash', 'family_id', 'device_id', 'allowed_actions',
                           'expires_at', 'used_count', 'max_uses', 'revoked', 'created_by']
                values = [token_id, token_hash, family_id, device_id, json.dumps(GUEST_ACTIONS),
                          local_to_utc_naive(effective_end), 0, stored_max_uses, 0, admin_uid]
                if has_valid_from:
                    columns.append('valid_from')
                    values.append(local_to_utc_naive(start_time))
                cursor.execute(
                    f"INSERT INTO guest_tokens ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(values))})",
                    values)

                audit = append_audit_log(
                    cursor, actor_id=admin_uid, actor_type='USER', family_id=family_id,
                    device_id=device_id, action='UC3.4_GUEST_TOKEN_ISSUED',
                    parameters={'authorization_id': token_id, 'token_hash': token_hash,
                                'allowed_actions': GUEST_ACTIONS,
                                'start_time': ledger_time(start_time), 'end_time': ledger_time(effective_end),
                                'max_uses': max_uses, 'credential_kind': 'GUEST_TOKEN_QR'},
                    status='Verified', decision='ALLOW', reason='GUEST_QR_AUTHORIZATION_ISSUED')
                ledger_event = enqueue_ledger_event(
                    cursor, uc_id='UC3.4', event_type='GUEST_TOKEN_ISSUED',
                    dedup_key=f'UC3.4:GUEST_TOKEN_ISSUED:TOKEN:{token_id}',
                    family_id=family_id,
                    gateway_id=resolve_gateway_id(cursor, family_id=family_id,
                                                  preferred_gateway_id=gateway_hint, device_id=device_id),
                    device_id=device_id,
                    created_by=admin_uid, source='SERVER',
                    actor={'actor_type': 'USER', 'actor_id_hash': hash_identifier(admin_uid), 'actor_role': 'ADMIN'},
                    payload={'authorization_id': token_id, 'credential_kind': 'GUEST_TOKEN_QR',
                             'token_hash': f'sha256:{token_hash}',
                             'authorization_scope': {'scope_type': 'DEVICE' if device_id else 'FAMILY',
                                                     'family_id': family_id, 'device_id': device_id,
                                                     'allowed_actions': GUEST_ACTIONS,
                                                     'role': 'GUEST', 'policy_enforced_by': 'SERVER'},
                             'validity': {'valid_from': ledger_time(start_time),
                                          'expires_at': ledger_time(effective_end)},
                             'usage_limit': {'max_uses': max_uses}, 'authorization_status': 'ACTIVE'})
                conn.commit()

            # 5. token 放 fragment：不會送到伺服器、不進 access log / Referer
            control_url = f"{base_url}#t={token_plain}"

            response_json({
                "status": "Success",
                "msg": "已核發訪客通行碼，請將 QR 提供給訪客",
                "data": {
                    "control_url": control_url,
                    "authorization_id": token_id,
                    "device_id": device_id,
                    "allowed_actions": GUEST_ACTIONS,
                    # UTC 帶 Z：App 的 asDateTime() 把不帶時區的字串當 UTC，
                    # 回台灣時間字串會被顯示成晚 8 小時
                    "start_time": ledger_time(start_time),
                    "end_time": ledger_time(effective_end),
                    "max_uses": max_uses,
                    "audit_log": audit,
                    "ledger_event": ledger_event,
                }
            })

        except pymysql.MySQLError:
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
