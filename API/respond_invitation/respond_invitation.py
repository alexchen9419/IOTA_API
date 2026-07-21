#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
import pymysql
import os
from dotenv import load_dotenv

load_dotenv()
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_USER = os.getenv('DB_USER', 'vboxuser')
DB_PASS = os.getenv('DB_PASS')
DB_NAME = os.getenv('DB_NAME', 'devicemanagement')
MQTT_HOST = os.getenv('MQTT_HOST', '192.168.0.84')

sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')

def response_json(data, status_code=200):
    print(f"Status: {status_code}")
    print("Content-Type: application/json; charset=utf-8\n")
    print(json.dumps(data, ensure_ascii=False))
    sys.exit()

def main():
    try:
        raw_data = sys.stdin.read()
        if not raw_data:
            response_json({"status": "Error", "msg": "無輸入資料"}, 400)
        
        request_data = json.loads(raw_data)
        payload = request_data.get("payload", {})

        # 接收參數
        invitation_id = payload.get("invitation_id")
        current_uid = payload.get("user_id") # 操作者本人(受邀者)
        action = payload.get("action")       # 動作：'Accept' 或 'Reject'

        if not all([invitation_id, current_uid, action]):
            response_json({"status": "Error", "msg": "缺少必要參數(invitation_id, user_id, action)"}, 400)

        if action not in ['Accept', 'Reject']:
            response_json({"status": "Error", "msg": "無效的 action 參數，僅接受 'Accept' 或 'Reject'"}, 400)

        conn = pymysql.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASS,
            database=DB_NAME, charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

        try:
            with conn.cursor() as cursor:
                # 1. 驗證該邀請是否存在且處於 Pending 狀態
                cursor.execute(
                    "SELECT family_id, role, status FROM family_invitations WHERE id = %s AND invitee_uid = %s",
                    (invitation_id, current_uid)
                )
                invitation = cursor.fetchone()
                
                if not invitation:
                    response_json({"status": "Error", "msg": "找不到此邀請或無權限操作"}, 404)
                if invitation['status'] != 'Pending':
                    response_json({"status": "Error", "msg": f"此邀請已被處理過 (目前狀態: {invitation['status']})"}, 409)
                
                # 2. 處理「拒絕」
                if action == 'Reject':
                    cursor.execute(
                        "UPDATE family_invitations SET status = 'Rejected' WHERE id = %s",
                        (invitation_id,)
                    )
                    conn.commit()
                    response_json({"status": "Success", "msg": "已拒絕該邀請"})
                    
                # 3. 處理「接受」 (啟動 Transaction 保證資料一致性)
                elif action == 'Accept':
                    family_id = invitation['family_id']
                    role = invitation['role']
                    
                    # 步驟 A: 將邀請狀態更新為 Accepted
                    cursor.execute(
                        "UPDATE family_invitations SET status = 'Accepted' WHERE id = %s",
                        (invitation_id,)
                    )
                    
                    # 步驟 B: 將使用者正式加入 user_families 表
                    cursor.execute(
                        "INSERT INTO user_families (user_id, family_id, role) VALUES (%s, %s, %s)",
                        (current_uid, family_id, role)
                    )
                    
                    conn.commit()
                    
                    # ==========================================
                    # 步驟 C: 依據 IoT-MQTT-Env 規範發布 MQTT 訊息
                    # ==========================================
                    try:
                        import paho.mqtt.client as mqtt
                        
                        # 1. 準備 Payload
                        mqtt_payload = {
                            "event": "MEMBER_ADDED",
                            "user_data": {
                                "user_id": current_uid,
                                "role": role
                            }
                        }
                        
                        # 2. 設定 MQTT Client 並連線 (匿名登入，localhost:1883)
                        client = mqtt.Client()
                        client.connect("192.168.0.84", 1883, 60)
                        
                        # 3. 配合專案規範設定 Topic
                        # 核心主題路徑：home/security/#
                        topic = f"home/security/gateway_{family_id}/auth_sync"
                        
                        # 發布訊息，QoS=1 確保送達
                        client.publish(topic, json.dumps(mqtt_payload), qos=1)
                        
                        # 斷開連線
                        client.disconnect()
                        
                    except Exception as mqtt_err:
                        # 網路抖動導致 MQTT 發送失敗時，不影響雲端資料庫已接受邀請的結果
                        pass 
                    # ==========================================

                    response_json({
                        "status": "Success", 
                        "msg": "已成功接受邀請並加入家庭，權限已透過 MQTT 推送",
                        "data": {
                            "family_id": family_id,
                            "role": role
                        }
                    })
                    
                    response_json({
                        "status": "Success", 
                        "msg": "已成功接受邀請並加入家庭",
                        "data": {
                            "family_id": family_id,
                            "role": role
                        }
                    })

        except pymysql.MySQLError as e:
            conn.rollback() # 發生錯誤，撤回所有資料庫變動
            
            if e.args[0] == 1062: # MySQL 重複鍵值錯誤
                 response_json({"status": "Error", "msg": "系統異常：您似乎已經是該家庭的成員"}, 409)
            else:
                 response_json({"status": "Error", "msg": "資料庫操作失敗", "detail": str(e)}, 500)
        finally:
            conn.close()

    except Exception as e:
        response_json({"status": "Error", "msg": "伺服器內部錯誤", "detail": str(e)}, 500)

if __name__ == "__main__":
    main()
