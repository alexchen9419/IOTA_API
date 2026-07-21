import json
import yaml
import time
from pathlib import Path

_DIR = Path(__file__).parent
MODELS = yaml.safe_load((_DIR / "models.yaml").read_text())
DEVICES_FILE = _DIR / "devices.json"


def load_devices():
    if DEVICES_FILE.exists():
        return json.loads(DEVICES_FILE.read_text())
    return {}


def save_devices(devices):
    DEVICES_FILE.write_text(json.dumps(devices, indent=2))


def register(client, payload):
    mac = payload["mac"]
    model = payload["model"]

    if model not in MODELS:
        print(f"[WARN] 未知型號: {model}")
        return None

    devices = load_devices()
    already_registered = mac in devices and devices[mac].get("model") == model

    devices[mac] = {
        "model": model,
        "last_seen": time.time(),
        "online": True
    }
    save_devices(devices)

    if already_registered:
        # 裝置已註冊過（devices.json 有紀錄），視為重連：更新 last_seen 即可，
        # config 早就 retain 在 broker 上，裝置 subscribe 時會自動收到，不用重發
        print(f"[INFO] 裝置重新連線（已註冊過）: {mac} ({model})")
    else:
        topic = f"home/device/{mac}/config"
        client.publish(topic, json.dumps(MODELS[model]), retain=True)
        print(f"[INFO] 註冊成功: {mac} ({model})")

    return MODELS[model]
