#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import pymysql
import paho.mqtt.client as mqtt
import os
from dotenv import load_dotenv

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
        client.subscribe("home/security/gateway_+/alert")
        print("[雲端路由] 開始監聽全域異常警報...")
    else:
        print(f"連線失敗，回傳碼: {rc}")

def on_message(client, userdata, msg):
    try:
        # 從 Topic 中動態解析出 family_id
        # 格式範例: home/security/gateway_12/alert
        topic_parts = msg.topic.split('/')
        family_id = topic_parts[2].replace('gateway_', '')
        
        payload_str = msg.payload.decode('utf-8')
        payload = json.loads(payload_str)
        
        print(f"\n[收到警報] 來自家庭 {family_id}: {payload.get('msg')}")
        
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
            payload['notification_target'] = uid
            client.publish(user_topic, json.dumps(payload), qos=1)
            print(f"  -> 已派發至 {user_topic}")
            
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