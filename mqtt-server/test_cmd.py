"""模擬發送 unlock / lock 指令，並監聽裝置狀態回報"""
import json
import os
import time
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

BROKER = os.getenv("MQTT_BROKER", "localhost")
MAC = "E8:31:CD:82:80:C8"


def on_connect(client, userdata, connect_flags, reason_code, properties):
    print(f"[TEST] 連線成功 (rc={reason_code})")
    client.subscribe(f"home/device/{MAC}/cmd")
    client.subscribe(f"home/device/{MAC}/state")


def on_message(client, userdata, msg):
    payload = json.loads(msg.payload.decode())
    print(f"[TEST] 收到訊息 ({msg.topic}): {payload}")


client = mqtt.Client(CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER, 1883)
client.loop_start()

time.sleep(1)  # 等待連線建立

# 發送 unlock 指令
cmd = json.dumps({"action": "unlock"})
client.publish(f"home/device/{MAC}/cmd", cmd)
print(f"[TEST] 已發送指令: {cmd}")
time.sleep(0.5)

# 模擬裝置狀態回報
client.publish(f"home/device/{MAC}/state", json.dumps({"locked": False}))
print("[TEST] 模擬裝置回報: locked=false")
time.sleep(0.5)

# 發送 lock 指令
cmd = json.dumps({"action": "lock"})
client.publish(f"home/device/{MAC}/cmd", cmd)
print(f"[TEST] 已發送指令: {cmd}")
time.sleep(0.5)

# 模擬裝置狀態回報
client.publish(f"home/device/{MAC}/state", json.dumps({"locked": True}))
print("[TEST] 模擬裝置回報: locked=true")
time.sleep(0.5)

client.loop_stop()
client.disconnect()
print("\n[TEST] 指令測試完成 ✓")
