#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import pymysql
import paho.mqtt.client as mqtt
import os
import re
import sys
from pathlib import Path
from dotenv import load_dotenv

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
from common.security_anomaly_service import record_security_anomaly

# 載入雲端環境變數
load_dotenv()
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_USER = os.getenv('DB_USER', 'vboxuser')
DB_PASS = os.getenv('DB_PASS', '82451258')
DB_NAME = os.getenv('DB_NAME', 'database02')
MQTT_HOST = os.getenv('MQTT_HOST', '192.168.0.84')

def get_authorized_users(family_id):
    """查詢具備接收通知權限的使用者名單"""
    conn = pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS,
        database=DB_NAME, charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )
    authorized_users = []
    try:
        with conn.cursor() as cursor:
            # 僅篩選 Admin 與 Member，排除 Guest (訪客不需要收到警報)
            sql = """
                SELECT user_id, role 
                FROM user_families 
                WHERE family_id = %s AND role IN ('Admin', 'Member')
            """
            cursor.execute(sql, (family_id,))
            results = cursor.fetchall()
            authorized_users = [row['user_id'] for row in results]
    except Exception as e:
        print(f"[資料庫錯誤] {e}")
    finally:
        conn.close()
        
    return authorized_users

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[雲端路由] 成功連線至 MQTT Broker!")
        # 訂閱所有家庭的警報通道
        client.subscribe("home/security/+/alert", qos=1)
        print("[雲端路由] 開始監聽全域異常警報...")
    else:
        print(f"連線失敗，回傳碼: {rc}")

def on_message(client, userdata, msg):
    try:
        # 從 Topic 中動態解析出 family_id
        # 格式範例: home/security/gateway_12/alert
        topic_match = re.fullmatch(r"home/security/gateway_([0-9]+)/alert", msg.topic)
        if not topic_match:
            print("[拒絕警報] Topic 格式不符。")
            return
        family_id = int(topic_match.group(1))
        
        payload_str = msg.payload.decode('utf-8')
        payload = json.loads(payload_str)
        if not isinstance(payload, dict):
            print("[拒絕警報] Payload 必須是 JSON object。")
            return
        if 'family_id' in payload and str(payload['family_id']).strip() != str(family_id):
            print("[拒絕警報] Payload family_id 與 Topic 不一致。")
            return
        
        print(f"\n[收到警報] 來自家庭 {family_id}: {payload.get('msg')}")

        # 舊格式仍可通知；記錄需要 Gateway 提供可重送的穩定識別碼。
        if str(payload.get('request_id') or '').strip() and str(payload.get('anomaly_type') or '').strip():
            conn = None
            try:
                conn = pymysql.connect(
                    host=DB_HOST, user=DB_USER, password=DB_PASS,
                    database=DB_NAME, charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor, autocommit=False,
                )
                notification = payload.get('notification')
                notification = dict(notification) if isinstance(notification, dict) else {}
                # 記錄時尚未送出本次通知，不能宣稱已派發。
                notification['notification_dispatched'] = False
                notification['notified_roles'] = ['ADMIN', 'MEMBER']
                result = record_security_anomaly(conn, {
                    **payload, 'family_id': family_id, 'source': 'GATEWAY',
                    'notification': notification,
                })
                state = '新增' if result['created'] else '已存在'
                print(f"[異常紀錄] {state}: {result['security_event_id']}")
            except Exception:
                print("[異常紀錄失敗] 本次未完成紀錄，繼續嘗試通知；請檢查資料庫與警報欄位。")
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        print("[資料庫錯誤] 異常紀錄連線關閉失敗；繼續嘗試通知。")
        else:
            print("[略過異常紀錄] 缺少 request_id 或 anomaly_type；保留原有通知流程。")
        
        # 查詢具備權限的成員
        users = get_authorized_users(family_id)
        
        if not users:
            print(f"[通知分發] 家庭 {family_id} 沒有管理員或成員可接收通知。")
            return
            
        print(f"[通知分發] 準備推播給以下使用者: {users}")
        
        # 分發個人化推播
        for uid in users:
            user_topic = f"app/user/{uid}/notifications"
            # 標記這是推播訊息
            user_payload = {**payload, 'notification_target': uid}
            try:
                info = client.publish(user_topic, json.dumps(user_payload), qos=1)
                if info.rc == mqtt.MQTT_ERR_SUCCESS:
                    print(f"  -> 已排入 MQTT 發送佇列: {user_topic}")
                else:
                    print(f"  -> MQTT 排入失敗: {user_topic}, rc={info.rc}")
            except Exception as exc:
                print(f"  -> MQTT 排入失敗: {user_topic}, {exc}")
            
    except Exception as e:
        print(f"[處理錯誤] {e}")

if __name__ == "__main__":
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    print(f"正在啟動警報路由服務，連接至 {MQTT_HOST}...")
    try:
        client.connect(MQTT_HOST, 1883, 60)
        client.loop_forever()
    except Exception as e:
        print(f"無法連線: {e}")
