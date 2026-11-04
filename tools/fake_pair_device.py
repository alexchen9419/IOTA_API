#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模擬一台進入配對模式的裝置，用來在韌體還沒做配對前測試 App 的「新增裝置」頁。

照 docs/PAIRING.md 的裝置端行為：
  1. 每 3 秒發 home/pair/announce {"mac","model","fw","nonce","ts"}，最多 30 次（約 90 秒）
  2. 收到 home/pair/<mac>/accept 且 nonce 相符 → 回 home/pair/<mac>/result {"ok":true}
  3. 回報後停止 announce 並結束

只模擬配對握手。配對完要讓這台裝置能被控制，另外跑 mqtt-server/fake_device.py。

用法（在 repo 根目錄）：
    # broker 不收匿名，用裝置帳號（密碼 = .env 的 MQTT_DEVICE_PASSWORD）
    #   $env:MQTT_USERNAME='device'; $env:MQTT_PASSWORD='<MQTT_DEVICE_PASSWORD>'
    # 本機開發（明文 1883）
    python tools/fake_pair_device.py AA:BB:CC:DD:EE:01 --broker 192.168.0.84 --port 1883
    # 走 TLS 8883
    $env:MQTT_USE_TLS='1'; $env:MQTT_CA_CERT='config/certs/ca.crt'
    python tools/fake_pair_device.py AA:BB:CC:DD:EE:01 --broker 192.168.0.84 --port 8883 --tls
    # 測 App 的逾時處理：收到 accept 也不回應
    python tools/fake_pair_device.py AA:BB:CC:DD:EE:02 --no-reply
"""
import argparse
import json
import secrets
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mqtt-server"))
import mqtt_tls  # noqa: E402

ANNOUNCE_INTERVAL = 3
MAX_ANNOUNCES = 30


def main() -> int:
    ap = argparse.ArgumentParser(description="模擬配對模式中的裝置（docs/PAIRING.md）")
    ap.add_argument("mac", help="裝置 MAC，例如 AA:BB:CC:DD:EE:01")
    ap.add_argument("--model", default="SMART-LOCK-V1")
    ap.add_argument("--fw", default="1.0.0-sim")
    ap.add_argument("--broker", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--tls", action="store_true", help="用 MQTT_CA_CERT 走 TLS")
    ap.add_argument("--no-reply", action="store_true", help="收到 accept 不回 result（測逾時）")
    args = ap.parse_args()

    mac = args.mac.strip().upper().replace("-", ":")
    nonce = secrets.token_hex(8)
    done = threading.Event()

    client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=f"pair-sim-{mac}")
    if args.tls:
        mqtt_tls.apply_tls(client)
    else:
        mqtt_tls.apply_auth(client)

    def on_connect(c, userdata, flags, rc, properties=None):
        if rc != 0:
            print(f"[SIM] 連線失敗 rc={rc}（帳密或 ACL？）")
            done.set()
            return
        c.subscribe(f"home/pair/{mac}/accept", qos=1)
        print(f"[SIM] 已連線，進入配對模式 mac={mac} nonce={nonce}")

    def on_message(c, userdata, msg):
        if not msg.payload:
            return  # 伺服器清 retained accept 時送的空訊息
        try:
            payload = json.loads(msg.payload)
        except ValueError:
            return
        if payload.get("nonce") not in (None, nonce):
            print(f"[SIM] 忽略 nonce 不符的 accept：{payload}")
            return
        print(f"[SIM] 收到配對確認：family={payload.get('family_id')}")
        if args.no_reply:
            print("[SIM] --no-reply：不回 result，App 應在逾時後取消")
            return
        c.publish(f"home/pair/{mac}/result",
                  json.dumps({"ok": True, "mac": mac, "nonce": nonce}), qos=1)
        print("[SIM] 已回報 result ok，配對完成")
        done.set()

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.broker, args.port, keepalive=30)
    client.loop_start()
    try:
        for i in range(MAX_ANNOUNCES):
            if done.is_set():
                break
            client.publish("home/pair/announce", json.dumps({
                "mac": mac, "model": args.model, "fw": args.fw,
                "nonce": nonce, "ts": int(time.time()),
            }))
            print(f"[SIM] announce {i + 1}/{MAX_ANNOUNCES}")
            done.wait(ANNOUNCE_INTERVAL)
        else:
            print("[SIM] 90 秒沒被配對，離開配對模式（PAIR_TIMEOUT）")
        time.sleep(1)  # 讓 result 送出去
    except KeyboardInterrupt:
        print("[SIM] 結束")
    finally:
        client.loop_stop()
        client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
