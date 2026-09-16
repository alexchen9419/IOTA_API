#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
建立家庭 / 場域 API

用途：
- 讓剛註冊完的使用者能自己開一個場域，成為該場域的 Admin。

背景：原本整套系統沒有任何「建立家庭」的 endpoint（WHITEPAPER 第 11 節與
README 都記載這個缺口，測試時只能手動 INSERT INTO families）。這造成 App
的死路：新使用者註冊完之後，除非有人邀請他，否則永遠停在「目前沒有加入
任何場域」的空畫面，連帶所有裝置/控制功能都無從測試。

流程（單一 transaction，兩張表要嘛都成功要嘛都不寫）：
  1. families      插入一列，admin_uid = 建立者
  2. user_families 插入一列，role = 'Admin'

命名衝突處理：同一位使用者底下不允許重複的家庭名稱（回 409），
避免場域切換時出現兩個看起來一模一樣的選項。不同使用者之間不限制。

POST /create_family
{"payload": {"user_id": "admin001", "family_name": "鶯歌老家"}}
"""

import json
import os
import sys
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

# families.family_name 是 VARCHAR(191)，這裡先擋掉超長輸入避免 MySQL 截斷。
MAX_NAME_LENGTH = 191
# 單一使用者可建立的場域數量上限，純粹防呆（避免誤觸迴圈灌爆資料表）。
MAX_FAMILIES_PER_USER = 20


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


def main() -> None:
    try:
        raw_data = sys.stdin.read()
        if not raw_data:
            response_json({"status": "Error", "msg": "無輸入資料"}, 400)

        payload = json.loads(raw_data).get("payload", {})

        user_id = str(payload.get("user_id") or payload.get("admin_uid") or "").strip()
        family_name = str(payload.get("family_name") or "").strip()

        if not user_id or not family_name:
            response_json(
                {"status": "Error", "msg": "缺少必要參數(user_id, family_name)"}, 400
            )

        if len(family_name) > MAX_NAME_LENGTH:
            response_json(
                {
                    "status": "Error",
                    "msg": f"場域名稱過長（上限 {MAX_NAME_LENGTH} 字）",
                },
                400,
            )

        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                # 1. 建立者必須是有效帳號。
                cursor.execute(
                    "SELECT user_id, status FROM users WHERE user_id = %s", (user_id,)
                )
                user = cursor.fetchone()
                if not user:
                    response_json({"status": "Error", "msg": "找不到此帳號"}, 404)
                if user["status"] != "Active":
                    response_json(
                        {"status": "Error", "msg": "此帳號已被停用，無法建立場域"}, 403
                    )

                # 2. 同一位使用者底下不允許同名場域。
                cursor.execute(
                    """
                    SELECT f.id
                    FROM families f
                    JOIN user_families uf ON uf.family_id = f.id
                    WHERE uf.user_id = %s AND f.family_name = %s
                    """,
                    (user_id, family_name),
                )
                if cursor.fetchone():
                    response_json(
                        {"status": "Error", "msg": f"您已經有一個名為「{family_name}」的場域"},
                        409,
                    )

                # 3. 數量上限防呆。
                cursor.execute(
                    "SELECT COUNT(*) AS c FROM user_families WHERE user_id = %s",
                    (user_id,),
                )
                if cursor.fetchone()["c"] >= MAX_FAMILIES_PER_USER:
                    response_json(
                        {
                            "status": "Error",
                            "msg": f"已達場域數量上限（{MAX_FAMILIES_PER_USER}）",
                        },
                        409,
                    )

                # 4. 建立家庭 + 把建立者設為 Admin。
                cursor.execute(
                    "INSERT INTO families (family_name, admin_uid) VALUES (%s, %s)",
                    (family_name, user_id),
                )
                family_id = cursor.lastrowid

                cursor.execute(
                    """
                    INSERT INTO user_families (user_id, family_id, role)
                    VALUES (%s, %s, 'Admin')
                    """,
                    (user_id, family_id),
                )

                conn.commit()

            response_json(
                {
                    "status": "Success",
                    "msg": "場域建立成功",
                    "data": {
                        "family_id": family_id,
                        "family_name": family_name,
                        "admin_uid": user_id,
                        "user_role": "Admin",
                    },
                },
                201,
            )

        except pymysql.MySQLError as e:
            conn.rollback()
            response_json(
                {"status": "Error", "msg": "資料庫操作失敗", "detail": str(e)}, 500
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
