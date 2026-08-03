"""模擬 ESP32 上線，發送註冊訊息，並監聽 server 回傳的設定"""
import json
import os
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
import mqtt_tls

BROKER = os.getenv("MQTT_BROKER", "localhost")
MAC = "11:11:11:11:11:11"
MODEL = "SMART-STRONGBOX-V1"

received_config = {}


def on_connect(client, userdata, connect_flags, reason_code, properties):
    print(f"[TEST] 連線成功 (rc={reason_code})")
    client.subscribe(f"home/device/{MAC}/config")
    payload = json.dumps({"model": MODEL, "mac": MAC})
    client.publish("home/register", payload)
    print(f"[TEST] 已發送註冊: {payload}")


def on_message(client, userdata, msg):
    print(f"[TEST] 收到設定 ({msg.topic}):")
    config = json.loads(msg.payload.decode())
    print(json.dumps(config, indent=2, ensure_ascii=False))
    received_config.update(config)
    client.disconnect()


client = mqtt.Client(CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message
mqtt_tls.apply_tls(client)
client.connect(BROKER, mqtt_tls.broker_port())
client.loop_forever()

print("\n[TEST] 測試完成" + (" ✓" if received_config else " ✗ 未收到設定"))
