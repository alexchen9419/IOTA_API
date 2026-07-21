#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC2.1 裝置清單與最近上鏈紀錄查詢 API

用途：
- 讓 App 查詢目前已完成 UC2.1 配對的裝置清單。
- 讓 App 顯示最近的 DEVICE_REGISTERED 稽核紀錄。

本檔案只查詢 database01.sql 已建立好的資料表，不做自動建表或補欄位。

支援 GET 與 POST 兩種呼叫方式：

GET 範例：
/cgi-bin/list_devices.py?owner_user_id=admin001&family_id=1

POST JSON 範例：
{
  "payload": {
    "owner_user_id": "admin001",
    "family_id": 1
  }
}
"""

import json
import os
import sys
import urllib.parse
from typing import Any, Dict

import pymysql
from dotenv import load_dotenv

# 讀取 .env 的資料庫連線設定。
load_dotenv()
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "vboxuser")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME", "devicemanagement")

# 指定標準輸入/輸出使用 UTF-8，避免中文裝置名稱或訊息亂碼。
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")


def response_json(data: Dict[str, Any], status_code: int = 200) -> None:
    """
    統一輸出 JSON 回應。

    default=str 用來處理 datetime 等無法直接 JSON 序列化的資料型態。
    例如 paired_at、last_update 從 MySQL 取出時可能是 datetime 物件。
    """
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8")
    print("Access-Control-Allow-Origin: *")
    print("Access-Control-Allow-Methods: GET, POST, OPTIONS")
    print("Access-Control-Allow-Headers: Content-Type, Authorization\n")
    print(json.dumps(data, ensure_ascii=False, default=str))
    sys.exit()


def get_conn():
    """
    建立 MySQL 連線。

    cursorclass 使用 DictCursor，查詢結果會是 dict 格式，
    方便直接轉成 JSON 回傳給 Flutter App。
    """
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def read_payload() -> Dict[str, Any]:
    """
    讀取前端傳入參數。

    支援兩種方式：
    1. POST：從 stdin 讀 JSON body，取出 payload。
    2. GET：從 QUERY_STRING 讀 URL query 參數。

    這樣 App 可以用 POST，瀏覽器或 curl 測試時也可以直接用 GET。
    """
    method = os.environ.get("REQUEST_METHOD", "GET").upper()

    if method == "POST":
        raw_data = sys.stdin.read()
        if not raw_data:
            return {}
        return json.loads(raw_data).get("payload", {})

    # CGI 環境下，GET query 會出現在 QUERY_STRING。
    query = os.environ.get("QUERY_STRING", "")
    parsed = urllib.parse.parse_qs(query)

    # parse_qs 會把每個值包成 list，例如 {'owner_user_id': ['admin001']}。
    # 這裡取第一個值，轉成一般 dict。
    return {key: value[0] for key, value in parsed.items() if value}


def main() -> None:
    """
    API 主流程。

    查詢內容分成兩部分：
    1. devices：目前符合條件的裝置清單。
    2. logs：最近 20 筆 DEVICE_REGISTERED 稽核紀錄。
    """
    try:
        payload = read_payload()

        # owner_user_id 用來查詢某位 Admin 底下的裝置。
        # family_id 用來限制查詢特定家庭/場域。
        owner_user_id = payload.get("owner_user_id") or payload.get("user_id")
        family_id = payload.get("family_id")

        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                # 動態建立 WHERE 條件。
                # 若 owner_user_id / family_id 都沒有傳，則查詢所有裝置。
                where = []
                params = []
                if owner_user_id:
                    where.append("owner_user_id = %s")
                    params.append(owner_user_id)
                if family_id:
                    where.append("family_id = %s")
                    params.append(family_id)

                where_sql = f"WHERE {' AND '.join(where)}" if where else ""

                # 查詢已註冊/已配對裝置。
                # session_key_hash 可以讓 Admin 確認該裝置已經建立過安全配對，
                # 但不會暴露真正 session key。
                cursor.execute(
                    f"""
                    SELECT device_id, device_name, device_type, status, last_action, last_update,
                           family_id, gateway_id, owner_user_id, pairing_status, paired_at,
                           session_key_hash, revoked_at, revoked_by, revocation_reason
                    FROM devices
                    {where_sql}
                    ORDER BY last_update DESC
                    LIMIT 100
                    """,
                    params,
                )
                devices = cursor.fetchall()

                # 查詢最近的 UC2.1 註冊事件。
                # 此處只取 DEVICE_REGISTERED，避免混入其他 UC 的操作日誌。
                cursor.execute(
                    """
                    SELECT command_id, user_id, device_id, action, status, timestamp, prev_hash, current_hash
                    FROM audit_logs
                    WHERE action = 'DEVICE_REGISTERED'
                    ORDER BY timestamp DESC, command_id DESC
                    LIMIT 20
                    """
                )
                logs = cursor.fetchall()

            response_json({"status": "Success", "msg": "查詢成功", "data": {"devices": devices, "logs": logs}})
        finally:
            conn.close()

    except json.JSONDecodeError:
        response_json({"status": "Error", "msg": "JSON 格式錯誤"}, 400)
    except Exception as e:
        # 測試階段保留 detail，方便看出 SQL 或連線錯誤。
        # 正式版可移除 detail，避免洩漏系統細節。
        response_json({"status": "Error", "msg": "伺服器內部錯誤", "detail": str(e)}, 500)


if __name__ == "__main__":
    main()
