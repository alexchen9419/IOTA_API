#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Optional real-device worker for UC4.1 / UC4.2.

When CONTROL_MODE=mqtt, ESP32 publishes status to:
    home/{family_id}/device/{device_id}/status

Run this worker on Gateway/Raspberry Pi to update control_commands,
device_telemetry and audit_logs automatically.
"""
from __future__ import annotations

import json
import os
import signal
import sys
from typing import Any, Dict

import device_status_update


def parse_topic(topic: str) -> Dict[str, Any]:
    # Expected: home/{family_id}/device/{device_id}/status
    parts = topic.split("/")
    data: Dict[str, Any] = {}
    try:
        if len(parts) >= 5 and parts[0] == "home" and parts[2] == "device":
            data["family_id"] = int(parts[1])
            data["device_id"] = parts[3]
    except Exception:
        pass
    return data


def main() -> None:
    try:
        import paho.mqtt.client as mqtt  # type: ignore
    except ImportError:
        print("paho-mqtt is required: pip install paho-mqtt", file=sys.stderr)
        sys.exit(1)

    host = os.getenv("MQTT_HOST", "localhost")
    port = int(os.getenv("MQTT_PORT", "1883"))
    username = os.getenv("MQTT_USERNAME")
    password = os.getenv("MQTT_PASSWORD")
    use_tls = os.getenv("MQTT_USE_TLS", "0") == "1"
    topic_filter = os.getenv("MQTT_STATUS_TOPIC", "home/+/device/+/status")

    client = mqtt.Client(client_id=os.getenv("MQTT_CLIENT_ID", "gateway-uc4-status-worker"))
    if username:
        client.username_pw_set(username, password=password)
    if use_tls:
        client.tls_set()

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            print(f"Connected to MQTT {host}:{port}; subscribing {topic_filter}")
            client.subscribe(topic_filter, qos=1)
        else:
            print(f"MQTT connect failed rc={rc}", file=sys.stderr)

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("MQTT payload must be a JSON object")
            topic_data = parse_topic(msg.topic)
            for key, value in topic_data.items():
                payload.setdefault(key, value)
            result = device_status_update.handle_status(payload)
            print(json.dumps({"topic": msg.topic, "result": result}, ensure_ascii=False, default=str))
        except Exception as exc:
            print(f"Failed to process MQTT status {msg.topic}: {exc}", file=sys.stderr)

    client.on_connect = on_connect
    client.on_message = on_message

    running = True

    def stop(_signum, _frame):
        nonlocal running
        running = False
        client.disconnect()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    client.connect(host, port, keepalive=30)
    client.loop_start()
    try:
        while running:
            signal.pause()
    except AttributeError:
        # signal.pause is not available on some platforms.
        import time
        while running:
            time.sleep(1)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
