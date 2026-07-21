import json


def on_state(mac, payload):
    locked = payload.get("locked")
    print(f"[LOCK {mac}] 狀態: {'鎖上' if locked else '開啟'}")


def on_event(mac, payload):
    event_type = payload.get("type")
    if event_type == "doorbell":
        print(f"[LOCK {mac}] 🔔 有人按門鈴")
        # TODO: 推播通知（Telegram / ntfy）
    elif event_type == "tamper_detected":
        print(f"[LOCK {mac}] ⚠️  防拆警報！")
        # TODO: 緊急警報


def send_unlock(client, mac):
    topic = f"home/device/{mac}/cmd"
    client.publish(topic, json.dumps({"action": "unlock"}))


def send_lock(client, mac):
    topic = f"home/device/{mac}/cmd"
    client.publish(topic, json.dumps({"action": "lock"}))


def send_doorbell_ack(client, mac):
    topic = f"home/device/{mac}/cmd"
    client.publish(topic, json.dumps({"action": "doorbell_ack"}))
