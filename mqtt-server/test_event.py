"""模擬門鈴與防拆事件"""
import json
import os
import time
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

BROKER = os.getenv("MQTT_BROKER", "localhost")
MAC = "AA:BB:CC:DD:EE:FF"


def on_connect(client, userdata, connect_flags, reason_code, properties):
    print(f"[TEST] 連線成功 (rc={reason_code})")


client = mqtt.Client(CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.connect(BROKER, 1883)
client.loop_start()

time.sleep(1)  # 等待連線建立

# 模擬門鈴事件
doorbell = json.dumps({"type": "doorbell"})
client.publish(f"home/device/{MAC}/event", doorbell)
print(f"[TEST] 已發送門鈴事件: {doorbell}")
time.sleep(0.5)

# 模擬防拆事件
tamper = json.dumps({"type": "tamper_detected"})
client.publish(f"home/device/{MAC}/event", tamper)
print(f"[TEST] 已發送防拆事件: {tamper}")
time.sleep(0.5)

client.loop_stop()
client.disconnect()
print("\n[TEST] 事件測試完成 ✓")
