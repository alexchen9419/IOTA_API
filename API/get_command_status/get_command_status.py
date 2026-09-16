#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UC4.1 / UC4.2 控制指令狀態查詢 API（輪詢專用）

用途：
- 讓 App 在送出控制指令後，輪詢「這一筆指令執行到哪了」與「裝置最新的實體狀態」。

背景：
`CONTROL_MODE=mqtt` 時 /control_device 只會回 PUBLISHED（已發布到 broker），
裝置要過幾秒才會透過 /device_status_update 回報結果。App 沒有 WebSocket 推播，
只能主動輪詢。

原本 App 是拿 /dashboard 當輪詢對象，有兩個問題：

1. **稽核鏈污染**（主要原因）
   get_family_dashboard.py 每次呼叫都會寫一筆 DASHBOARD_VIEWED 進 audit_logs。
   App 每 2 秒輪詢一次、最多 15 次，於是「按一次解鎖」會在雜湊鏈裡留下多達
   15 筆假的「檢視儀表板」紀錄。本專題的核心賣點是不可篡改的稽核鏈，鏈上
   絕大多數卻是 App 輪詢產生的雜訊，稽核價值等於被稀釋掉。

2. **成本不對稱**
   /dashboard 會撈整個場域所有裝置、摘要統計與遙測歷史，而輪詢只需要一筆
   指令的狀態。

因此這支端點刻意設計成：
- 只查一筆 control_commands + 該裝置最新一筆 device_telemetry
- **不寫任何 audit_logs** —— 查詢自己剛送出的指令執行結果屬於同一次操作的
  延續，該次操作在 /control_device 時已經寫過 CONTROL_DEVICE 稽核紀錄了，
  重複記錄只會讓鏈變長而不會增加可稽核性

權限：呼叫者必須是該指令所屬場域的有效成員（角色非 Revoked）。這裡不放寬成
「只要知道 command_id 就能查」—— command_id 雖然難猜，但不該當成存取控制。

支援 GET 與 POST：

GET  /get_command_status?command_id=xxx&user_id=admin001
POST /get_command_status  {"payload": {"command_id": "xxx", "user_id": "admin001"}}
"""

import json
import os
import sys
import urllib.parse
from typing import Any, Dict

import pymysql
from dotenv import load_dotenv

load_dotenv()
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "vboxuser")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME", "devicemanagement")

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

# 與 get_user_families.py 一致：被撤銷權限的成員不算有效成員。
REVOKED_ROLES = ("Revoked", "revoked", "REVOKED")


def response_json(data: Dict[str, Any], status_code: int = 200) -> None:
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8\n")
    print(json.dumps(data, ensure_ascii=False, default=str))
    sys.exit()


def get_conn():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def read_payload() -> Dict[str, Any]:
    method = os.environ.get("REQUEST_METHOD", "GET").upper()

    if method == "POST":
        raw_data = sys.stdin.read()
        if not raw_data:
            return {}
        return json.loads(raw_data).get("payload", {})

    parsed = urllib.parse.parse_qs(os.environ.get("QUERY_STRING", ""))
    return {key: value[0] for key, value in parsed.items() if value}


def main() -> None:
    try:
        payload = read_payload()
        command_id = str(payload.get("command_id") or "").strip()
        user_id = str(payload.get("user_id") or "").strip()

        if not command_id or not user_id:
            response_json(
                {"status": "Error", "msg": "缺少必要參數(command_id, user_id)"}, 400
            )

        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT command_id, family_id, device_id, action, status,
                           reason, actor_id, created_at, published_at, completed_at
                    FROM control_commands
                    WHERE command_id = %s
                    """,
                    (command_id,),
                )
                command = cursor.fetchone()
                if not command:
                    response_json({"status": "Error", "msg": "找不到此指令"}, 404)

                # 場域成員資格檢查。不用 command 的 actor_id 比對，因為同一個
                # 場域的其他成員也應該看得到裝置目前在執行什麼。
                revoked_placeholders = ", ".join(["%s"] * len(REVOKED_ROLES))
                cursor.execute(
                    f"""
                    SELECT role FROM user_families
                    WHERE user_id = %s AND family_id = %s
                      AND role NOT IN ({revoked_placeholders})
                    """,
                    (user_id, command["family_id"], *REVOKED_ROLES),
                )
                if not cursor.fetchone():
                    response_json(
                        {"status": "Error", "msg": "您沒有查詢此指令的權限"}, 403
                    )

                # 裝置最新一筆遙測，讓 App 在指令完成時可以直接更新畫面上的
                # 實體狀態 / 電量 / 訊號，不必再打一次 /dashboard。
                cursor.execute(
                    """
                    SELECT physical_state, status, battery, rssi, recorded_at
                    FROM device_telemetry
                    WHERE device_id = %s
                    ORDER BY recorded_at DESC, id DESC
                    LIMIT 1
                    """,
                    (command["device_id"],),
                )
                telemetry = cursor.fetchone()

            response_json(
                {
                    "status": "Success",
                    "msg": "查詢成功",
                    "data": {"command": command, "telemetry": telemetry},
                }
            )
        finally:
            conn.close()

    except json.JSONDecodeError:
        response_json({"status": "Error", "msg": "JSON 格式錯誤"}, 400)
    except Exception as e:
        response_json(
            {"status": "Error", "msg": "伺服器內部錯誤", "detail": str(e)}, 500
        )


if __name__ == "__main__":
    main()
