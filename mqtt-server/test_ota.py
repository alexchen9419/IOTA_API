"""觸發指定裝置 OTA 更新：發布韌體下載網址到 home/device/<mac>/ota
用法：docker exec mqtt_server python test_ota.py [mac] [檔名] [版本號]
"""
import json
import os
import sys
import time
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

BROKER = os.getenv("MQTT_BROKER", "localhost")
# OTA_HOST：跑 mqtt-server 的電腦區網 IP，要跟 Arduino sketch 的 MQTT_BROKER 一致，
# 因為 ESP32 是用這個位址連回來下載 .bin（容器內部 IP 對 ESP32 沒用）
OTA_HOST = os.getenv("OTA_HOST", "192.168.1.3")

MAC = sys.argv[1] if len(sys.argv) > 1 else "E8:31:CD:82:80:C8"
FIRMWARE_FILE = sys.argv[2] if len(sys.argv) > 2 else "SMART-LOCK-V1.bin"
VERSION = sys.argv[3] if len(sys.argv) > 3 else "dev"


def on_connect(client, userdata, connect_flags, reason_code, properties):
    print(f"[TEST] 連線成功 (rc={reason_code})")


client = mqtt.Client(CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.connect(BROKER, 1883)
client.loop_start()
time.sleep(1)  # 等待連線建立

topic = f"home/device/{MAC}/ota"
payload = json.dumps({
    "url": f"http://{OTA_HOST}:8080/firmware/{FIRMWARE_FILE}",
    "version": VERSION,
})
client.publish(topic, payload)
print(f"[TEST] 已發送 OTA 指令 → {topic}: {payload}")
time.sleep(0.5)

client.loop_stop()
client.disconnect()
print("\n[TEST] OTA 觸發完成 ✓（裝置會開始下載並自動重開機）")
