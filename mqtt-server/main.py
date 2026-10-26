import json
import os
import time
import threading
import importlib
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
import registry
import mqtt_tls
import pairing

BROKER = os.getenv("MQTT_BROKER", "localhost")
active_handlers = {}  # mac → handler module

# ── OTA 韌體檔案伺服器（http://<host>:8080/firmware/<file>.bin）──────────────
FIRMWARE_DIR = Path(__file__).parent / "firmware"
FIRMWARE_DIR.mkdir(exist_ok=True)

http_app = FastAPI()
http_app.mount("/firmware", StaticFiles(directory=FIRMWARE_DIR), name="firmware")

_client = None


@http_app.get("/pair/pending")
def pair_pending():
    """待配對裝置清單（App 用來顯示「附近可加入的裝置」）。"""
    return {"ttl_seconds": pairing.PENDING_TTL, "devices": pairing.list_pending()}


@http_app.post("/pair/accept/{mac}")
async def pair_accept(mac: str, request: Request):
    """由 API／App 觸發：對指定 MAC 發出定向配對確認。"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    family_id = str(body.get("family_id") or "").strip()
    result = pairing.accept(_client, mac, family_id)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@http_app.post("/pair/cancel/{mac}")
def pair_cancel(mac: str):
    """取消配對：清 pending 與 retained accept。"""
    result = pairing.cancel(_client, mac)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


def _start_http_server():
    uvicorn.run(http_app, host="0.0.0.0", port=8080, log_level="warning")


def on_connect(client, userdata, connect_flags, reason_code, properties):
    print(f"[INFO] Broker 連線成功 (rc={reason_code})")
    client.subscribe("home/register")
    client.subscribe("home/device/+/state")
    client.subscribe("home/device/+/event")
    # 配對廣播：只在裝置進入配對模式時才會有訊息
    client.subscribe("home/pair/announce")
    client.subscribe("home/pair/+/result")


def on_message(client, userdata, msg):
    topic = msg.topic
    try:
        payload = json.loads(msg.payload.decode())
    except json.JSONDecodeError:
        print(f"[WARN] 無效 JSON: {msg.payload}")
        return

    parts = topic.split("/")

    if topic == "home/register":
        model_def = registry.register(client, payload)
        if model_def:
            mac = payload["mac"]
            handler_name = model_def.get("handler", "default")
            active_handlers[mac] = importlib.import_module(f"handlers.{handler_name}")
        return

    if topic == "home/pair/announce":
        pairing.handle_announce(payload)
        return

    if len(parts) == 4 and parts[1] == "pair" and parts[3] == "result":
        pairing.handle_result(client, parts[2], payload)
        return

    if len(parts) == 4 and parts[3] == "state":
        mac = parts[2]
        handler = active_handlers.get(mac)
        if handler:
            handler.on_state(mac, payload)

    if len(parts) == 4 and parts[3] == "event":
        mac = parts[2]
        handler = active_handlers.get(mac)
        if handler:
            handler.on_event(mac, payload)


client = mqtt.Client(CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message
mqtt_tls.apply_tls(client)
_client = client

# broker（容器）可能比 server 晚就緒，重試直到連上
while True:
    try:
        client.connect(BROKER, mqtt_tls.broker_port())
        break
    except OSError as e:
        print(f"[INFO] Broker ({BROKER}) 未就緒: {e}，2 秒後重試")
        time.sleep(2)

threading.Thread(target=_start_http_server, daemon=True).start()
client.loop_forever()
