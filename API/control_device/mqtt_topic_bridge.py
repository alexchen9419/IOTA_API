#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MQTT topic bridge between the API's assumed convention and the convention
actually used by mqtt-server/ + the real ESP32 firmware (Arduino/MqttSmartLock).

This file is new and does NOT modify control_device.py or
mqtt_status_worker.py. It exists because:

- control_device.py (CONTROL_MODE=mqtt) publishes commands to
  home/{family_id}/device/{device_id}/cmd with an uppercase action
  (e.g. "UNLOCK"). The real ESP32 firmware only listens on
  home/device/<mac>/cmd and only understands a lowercase
  {"action": "lock" | "unlock"}.
- The real ESP32 firmware reports lock state on home/device/<mac>/state as
  {"locked": bool}, and doorbell/tamper events on home/device/<mac>/event.
  device_status_update.handle_status() (existing, unmodified) expects
  {"family_id", "device_id", "status", "physical_state", ...}.

This bridge subscribes to both worlds' topics and translates between them,
calling the existing device_status_update.handle_status() directly instead
of re-publishing to home/{family_id}/device/{device_id}/status.

Run as its own long-lived process (see docker-compose service api-mqtt-bridge).
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pymysql
import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).parent))
import device_status_update  # existing, unmodified API module

ACTION_MAP = {"LOCK": "lock", "UNLOCK": "unlock"}


def get_db_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", os.getenv("MYSQL_USER", "vboxuser")),
        password=os.getenv("DB_PASSWORD", os.getenv("DB_PASS", os.getenv("MYSQL_PASSWORD", ""))),
        database=os.getenv("DB_NAME", os.getenv("MYSQL_DATABASE", "devicemanagement")),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def lookup_family_id(device_id: str) -> Optional[int]:
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT family_id FROM devices WHERE device_id=%s", (device_id,))
                row = cur.fetchone()
                return int(row["family_id"]) if row and row.get("family_id") is not None else None
        finally:
            conn.close()
    except Exception as exc:
        print(f"[bridge] DB lookup failed for {device_id}: {exc}", file=sys.stderr)
        return None


def write_event_audit(device_id: str, family_id: Optional[int], event_type: str) -> None:
    """doorbell / tamper_detected has no equivalent in the API's UC set yet,
    so record it directly in audit_logs using the same hash-chain style the
    rest of API/ already uses."""
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT current_hash FROM audit_logs ORDER BY id DESC LIMIT 1")
                row = cur.fetchone()
                prev_hash = row["current_hash"] if row and row.get("current_hash") else "0" * 64
                timestamp = int(time.time())
                command_id = f"EVT_{timestamp}_{device_id}"
                raw = json.dumps({
                    "command_id": command_id, "device_id": device_id, "family_id": family_id,
                    "action": "DEVICE_EVENT", "event_type": event_type, "timestamp": timestamp,
                    "prev_hash": prev_hash,
                }, sort_keys=True)
                current_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                cur.execute(
                    """
                    INSERT INTO audit_logs
                      (command_id, actor_id, actor_type, device_id, family_id, action,
                       parameters, status, decision, reason, prev_hash, current_hash, hash, timestamp)
                    VALUES (%s, %s, 'DEVICE', %s, %s, 'DEVICE_EVENT', CAST(%s AS JSON),
                            'SUCCEEDED', 'ALLOW', %s, %s, %s, %s, %s)
                    """,
                    (command_id, device_id, device_id, family_id,
                     json.dumps({"event_type": event_type}), event_type, prev_hash,
                     current_hash, current_hash, timestamp),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        print(f"[bridge] failed to write event audit for {device_id}: {exc}", file=sys.stderr)


def topic_parts(topic: str) -> List[str]:
    return topic.split("/")


def handle_api_cmd(client: mqtt.Client, topic: str, payload: Dict[str, Any]) -> None:
    # home/{family_id}/device/{device_id}/cmd  (published by control_device.py)
    p = topic_parts(topic)
    if len(p) != 5 or p[0] != "home" or p[2] != "device" or p[4] != "cmd":
        return
    device_id = p[3]
    action = str(payload.get("action", "")).strip().upper()
    mapped = ACTION_MAP.get(action)
    if not mapped:
        print(f"[bridge] {device_id}: action '{action}' not supported by firmware, dropped")
        return
    real_topic = f"home/device/{device_id}/cmd"
    client.publish(real_topic, json.dumps({"action": mapped}))
    print(f"[bridge] {topic} action={action} -> {real_topic} action={mapped}")


def handle_device_state(topic: str, payload: Dict[str, Any]) -> None:
    # home/device/<mac>/state  (published by the real ESP32 firmware)
    p = topic_parts(topic)
    if len(p) != 4 or p[1] != "device" or p[3] != "state":
        return
    device_id = p[2]
    family_id = lookup_family_id(device_id)
    if family_id is None:
        print(f"[bridge] {device_id}: not paired (no row in devices table), skipping state update")
        return
    locked = payload.get("locked")
    status_payload = {
        "family_id": family_id,
        "device_id": device_id,
        "status": "SUCCEEDED",
        "physical_state": "LOCKED" if locked else "UNLOCKED",
    }
    try:
        result = device_status_update.handle_status(status_payload)
        print(f"[bridge] {topic} -> device_status_update: {result}")
    except Exception as exc:
        print(f"[bridge] device_status_update failed for {device_id}: {exc}", file=sys.stderr)


def handle_device_event(topic: str, payload: Dict[str, Any]) -> None:
    # home/device/<mac>/event  (doorbell / tamper_detected)
    p = topic_parts(topic)
    if len(p) != 4 or p[1] != "device" or p[3] != "event":
        return
    device_id = p[2]
    family_id = lookup_family_id(device_id)
    event_type = payload.get("type", "unknown")
    write_event_audit(device_id, family_id, event_type)
    print(f"[bridge] {topic} event={event_type} -> audit_logs (family_id={family_id})")


def main() -> None:
    host = os.getenv("MQTT_HOST", "localhost")
    port = int(os.getenv("MQTT_PORT", "1883"))

    client = mqtt.Client(client_id="api-mqtt-topic-bridge")

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            print(f"[bridge] connected to {host}:{port}")
            client.subscribe("home/+/device/+/cmd", qos=1)
            client.subscribe("home/device/+/state", qos=1)
            client.subscribe("home/device/+/event", qos=1)
        else:
            print(f"[bridge] MQTT connect failed rc={rc}", file=sys.stderr)

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as exc:
            print(f"[bridge] bad payload on {msg.topic}: {exc}", file=sys.stderr)
            return

        p = topic_parts(msg.topic)
        try:
            if len(p) == 5 and p[2] == "device" and p[4] == "cmd":
                handle_api_cmd(client, msg.topic, payload)
            elif len(p) == 4 and p[1] == "device" and p[3] == "state":
                handle_device_state(msg.topic, payload)
            elif len(p) == 4 and p[1] == "device" and p[3] == "event":
                handle_device_event(msg.topic, payload)
        except Exception as exc:
            print(f"[bridge] error handling {msg.topic}: {exc}", file=sys.stderr)

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
        while running:
            time.sleep(1)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
