import json


def on_state(mac, payload):
    locked = payload.get("locked")
    alarm = payload.get("alarm")
    print(f"[STRONGBOX {mac}] 狀態: {'鎖上' if locked else '開啟'}", end="")
    if alarm:
        print(" | 警報觸發中", end="")
    print()


def on_event(mac, payload):
    event_type = payload.get("type")
    if event_type == "tamper_detected":
        print(f"[STRONGBOX {mac}] ⚠️  防拆警報！")
        # 推播在 API 端做：api-mqtt-bridge → home/security/gateway_<family>/alert → alert-service
    elif event_type == "alarm_triggered":
        print(f"[STRONGBOX {mac}] 🚨 警報響起！")
        # 同上


def send_unlock(client, mac):
    topic = f"home/device/{mac}/cmd"
    client.publish(topic, json.dumps({"action": "unlock"}))


def send_lock(client, mac):
    topic = f"home/device/{mac}/cmd"
    client.publish(topic, json.dumps({"action": "lock"}))


def send_alarm(client, mac, active: bool):
    topic = f"home/device/{mac}/cmd"
    client.publish(topic, json.dumps({"action": "alarm", "active": active}))
