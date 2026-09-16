#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模擬 ESP32 裝置，用來在實體硬體到場之前驗證整條軟體鏈路。

為什麼需要這支：
實體測試最貴的成本是「不知道問題出在韌體、WiFi、broker 還是後端」。這支把
軟體側（配對 → App → API → bridge → broker → 裝置 → 狀態回報 → 資料庫）
單獨跑通，真硬體接上來之後，剩下要煩惱的就只有 WiFi、TLS 與實體作動。

它確實抓到過東西：`control_commands.status` 永遠停在 PUBLISHED（韌體的
state 不帶 command_id），導致 App 每次控制都會跳 30 秒逾時。那個 bug 沒有
實體裝置也重現得出來。

## 行為由 models.yaml 決定，不是寫死的

型號的 features / default_state / auto_lock_sec / pins 全部讀 models.yaml，
所以新增型號時這支不用改：

    SMART-LOCK-V1       features: lock, doorbell, tamper_detect
    SMART-STRONGBOX-V1  features: lock, alarm, tamper_detect

features 也決定它「不做什麼」：SMART-STRONGBOX-V1 沒有 doorbell，要它送
doorbell 事件會被擋下來，跟真機一樣。

## 對照 Arduino/MqttSmartLock/MqttSmartLock.ino

  ✓ topic 命名          home/device/<mac>/{config,cmd,state,ota,event}
  ✓ 註冊封包            {"model": ..., "mac": ...} → home/register
  ✓ 連線後順序          subscribe → register → 回報目前狀態（applyLockState）
  ✓ 一次開機只註冊一次   斷線重連不重送（韌體的 registered 旗標）
  ✓ 狀態封包            {"locked": bool}；strongbox 另帶 alarm
  ✓ 事件封包            {"type": "doorbell" | "tamper_detected" | "alarm_triggered"}
  ✓ 自動上鎖            解鎖後 auto_lock_sec 秒自動上鎖並再發一次 state
  ✓ config 覆蓋         用 server 回傳的 retained config 覆蓋預設值

**沒有**模擬的部分（這些只能用實體裝置驗證）：

  ✗ TLS 8883 + 內嵌 CA + NTP 對時（這支走明文 1883）
  ✗ OTA：韌體下載、SHA-256、Ed25519 驗簽、分區切換
  ✗ 實體世界：servo 卡住、WiFi 掉線、觸控誤觸發、電源不穩

# ponytail: 指令解析用 JSON，韌體是字串比對（先找 "unlock" 再找 "lock"）。
# bridge 送出的一律是格式良好的 {"action": "..."}，兩者結果相同；真要測
# 韌體那套比對的邊界情況，得用實機。

## 用法

    # 常駐模擬一台裝置
    python fake_device.py <MAC> --broker 192.168.50.234

    # 指定型號（預設 SMART-LOCK-V1，可用的型號讀 models.yaml）
    python fake_device.py <MAC> --model SMART-STRONGBOX-V1

    # 全新裝置安全配對（UC2.1）：自己產生 SECP256R1 金鑰組，
    # 把公鑰送進 /device_pair 做真正的 ECDH，而不是讓後端拿模擬金鑰湊數
    python fake_device.py <MAC> --pair --family 4 --owner itadmin047698

    # 一次性送事件（型號的 features 要支援才送得出去）
    python fake_device.py <MAC> --event doorbell
"""
import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import paho.mqtt.client as mqtt
import yaml
from paho.mqtt.enums import CallbackAPIVersion

HERE = Path(__file__).resolve().parent
MODELS_FILE = HERE / "models.yaml"

# 事件名稱對應到 models.yaml 的 feature；不在型號 features 裡的事件不該送得出去。
EVENT_FEATURE = {
    "doorbell": "doorbell",
    "tamper_detected": "tamper_detect",
    "alarm_triggered": "alarm",
}


def load_models() -> dict:
    if not MODELS_FILE.exists():
        sys.exit(f"找不到 {MODELS_FILE}")
    return yaml.safe_load(MODELS_FILE.read_text(encoding="utf-8")) or {}


class FakeDevice:
    """一台由 models.yaml 定義的模擬裝置。"""

    def __init__(self, mac: str, model: str, model_def: dict, broker: str, port: int):
        self.mac = mac
        self.model = model
        self.features = list(model_def.get("features") or [])
        self.pins = model_def.get("pins") or {}
        # 韌體的 bool locked 初值來自 models.yaml 的 default_state
        self.locked = str(model_def.get("default_state", "locked")).lower() == "locked"
        self.auto_lock_sec = int(model_def.get("auto_lock_sec") or 0)
        self.alarm = False

        self.broker, self.port = broker, port
        self.t_config = f"home/device/{mac}/config"
        self.t_cmd = f"home/device/{mac}/cmd"
        self.t_state = f"home/device/{mac}/state"
        self.t_event = f"home/device/{mac}/event"
        self.t_ota = f"home/device/{mac}/ota"

        self.unlock_at = None
        self.registered = False          # 韌體的 registered 旗標
        self._lock = threading.Lock()
        self.client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=f"esp32-{mac}")
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def log(self, msg: str) -> None:
        print(f"[fake {self.mac}] {msg}", flush=True)

    def has(self, feature: str) -> bool:
        return feature in self.features

    # ---------- 狀態（對應韌體的 applyLockState）----------
    def state_payload(self) -> dict:
        p = {"locked": self.locked}
        # strongbox 的 handler 會讀 alarm 欄位（見 handlers/strongbox.py）
        if self.has("alarm"):
            p["alarm"] = self.alarm
        return p

    def publish_state(self) -> None:
        payload = json.dumps(self.state_payload())
        self.client.publish(self.t_state, payload)
        self.log(f"[STATE] {payload}")

    def apply_lock_state(self, locked: bool) -> None:
        with self._lock:
            self.locked = locked
            self.unlock_at = time.monotonic() if not locked else None
        self.log(f"   {'上鎖' if locked else '解鎖'}（{'servo' if 'servo' in self.pins else 'relay'}）")
        self.publish_state()

    def publish_event(self, event_type: str) -> bool:
        need = EVENT_FEATURE.get(event_type)
        if need and not self.has(need):
            self.log(f"[EVENT] {self.model} 沒有 {need} 功能，不送 {event_type}")
            return False
        payload = json.dumps({"type": event_type})
        self.client.publish(self.t_event, payload)
        self.log(f"[EVENT] {payload}")
        return True

    # ---------- MQTT ----------
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        self.log(f"[MQTT] 連線 {self.broker}:{self.port} ... {rc}")
        client.subscribe(self.t_config)
        client.subscribe(self.t_cmd)
        client.subscribe(self.t_ota)

        if not self.registered:
            reg = json.dumps({"model": self.model, "mac": self.mac})
            client.publish("home/register", reg)
            self.log(f"[REGISTER] {reg}")
            self.registered = True
        else:
            self.log("[REGISTER] 已註冊過，略過")

        self.publish_state()   # 韌體連上後會回報一次目前狀態

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except Exception:
            self.log(f"[WARN] 非 JSON: {msg.payload!r}")
            return

        if msg.topic == self.t_config:
            sec = payload.get("auto_lock_sec")
            if isinstance(sec, int) and sec > 0:
                self.auto_lock_sec = sec
            self.log(f"[CONFIG] 收到 retained config，auto_lock_sec={self.auto_lock_sec}"
                     f"，features={payload.get('features')}")
            return

        if msg.topic == self.t_ota:
            self.log(f"[OTA] {payload} —— 這支不模擬 OTA，忽略")
            return

        if msg.topic != self.t_cmd:
            return

        action = str(payload.get("action", "")).lower()
        self.log(f"[CMD] {payload}")

        if action in ("lock", "unlock"):
            if not self.has("lock"):
                self.log(f"   {self.model} 沒有 lock 功能，忽略")
                return
            time.sleep(0.5)                       # 模擬實體作動
            self.apply_lock_state(action == "lock")
        elif action == "alarm":
            if not self.has("alarm"):
                self.log(f"   {self.model} 沒有 alarm 功能，忽略")
                return
            self.alarm = bool(payload.get("active"))
            self.log(f"   警報 {'啟動' if self.alarm else '解除'}")
            self.publish_state()
            if self.alarm:
                self.publish_event("alarm_triggered")
        elif action == "doorbell_ack":
            self.log("   門鈴已確認")
        else:
            self.log(f"   未知指令: {action}")

    # ---------- 主迴圈（對應韌體的 loop）----------
    def run(self) -> None:
        self.client.connect(self.broker, self.port, keepalive=30)
        self.client.loop_start()
        self.log(f"啟動 model={self.model} features={self.features} "
                 f"default_state={'locked' if self.locked else 'unlocked'} "
                 f"auto_lock_sec={self.auto_lock_sec}")
        self.log("Ctrl+C 結束")
        try:
            while True:
                with self._lock:
                    due = (self.auto_lock_sec > 0 and not self.locked
                           and self.unlock_at is not None
                           and time.monotonic() - self.unlock_at >= self.auto_lock_sec)
                if due:
                    self.log(f"[AUTO] 超過 {self.auto_lock_sec} 秒，自動上鎖")
                    self.apply_lock_state(True)
                time.sleep(0.1)
        except KeyboardInterrupt:
            self.log("結束")
        finally:
            self.client.loop_stop()
            self.client.disconnect()


# ═══════════════════════════════════════════════════════════════
# UC2.1 全新裝置安全配對
# ═══════════════════════════════════════════════════════════════
def secure_pair(api: str, mac: str, model: str, model_def: dict,
                family_id: int, owner: str, name: str) -> bool:
    """以「真的有一台新裝置」的方式跑一次 /device_pair。

    device_pair.py 的 device_public_key_pem 可以省略，省略時後端會自己產生一組
    模擬 ESP32 金鑰、回應標記 simulated_device: true。App 目前就是這樣送的，
    所以現有的配對紀錄全部是模擬金鑰。

    這裡改成裝置自己產生 SECP256R1 金鑰組、只交出公鑰，讓後端跟真正的對端做
    ECDH，回應會是 simulated_device: false —— 這才是 UC2.1 描述的流程。
    私鑰只留在這支程式裡，跟真機一樣不外送。
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    priv = ec.generate_private_key(ec.SECP256R1())
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    body = json.dumps({"payload": {
        "owner_user_id": owner,
        "family_id": family_id,
        "device_id": mac,
        "device_name": name or f"{model} {mac[-5:]}",
        "device_type": model_def.get("handler", "smart_lock"),
        "device_public_key_pem": pub_pem,
    }}, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        api.rstrip("/") + "/device_pair", data=body,
        headers={"Content-Type": "application/json; charset=utf-8"})
    print(f"[pair] POST /device_pair  device_id={mac}  model={model}")
    print(f"[pair] 送出裝置公鑰（SECP256R1，私鑰不外送）")
    try:
        raw = urllib.request.urlopen(req, timeout=20).read().decode("utf-8")
    except urllib.error.HTTPError as e:
        print(f"[pair] 失敗 HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:400]}")
        return False
    except Exception as e:
        print(f"[pair] 連不上 {api}：{e}")
        return False

    res = json.loads(raw)
    if res.get("status") != "Success":
        print(f"[pair] 失敗：{res.get('msg') or res.get('message')}")
        return False

    d = res.get("data") or {}
    ledger = d.get("ledger") or {}
    print(f"[pair] 成功：{res.get('msg')}")
    print(f"       pairing_status   : {d.get('pairing_status')}")
    print(f"       simulated_device : {d.get('simulated_device')}  "
          f"（False = 後端用的是這台裝置真正的公鑰）")
    print(f"       device_side_verified : {d.get('device_side_verified')}")
    print(f"       session_key_hash : {str(d.get('session_key_hash'))[:32]}…")
    print(f"       稽核鏈 command_id : {ledger.get('command_id')}")
    print(f"       current_hash      : {str(ledger.get('current_hash'))[:32]}…")
    return True


def main() -> int:
    models = load_models()
    ap = argparse.ArgumentParser(description="模擬 ESP32 裝置（行為由 models.yaml 決定）")
    ap.add_argument("mac", help="裝置 MAC，要與 App 配對時填的識別碼一致")
    ap.add_argument("--model", default="SMART-LOCK-V1",
                    help="型號，必須存在於 models.yaml：" + "、".join(models))
    ap.add_argument("--broker", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--api", default="http://localhost:8091", help="--pair 用的 API 位址")
    ap.add_argument("--pair", action="store_true",
                    help="先跑一次 UC2.1 安全配對（自己產金鑰、送公鑰）再進入常駐模式")
    ap.add_argument("--family", type=int, help="--pair 用：要配對進哪個場域")
    ap.add_argument("--owner", help="--pair 用：操作的 Admin 帳號")
    ap.add_argument("--name", default="", help="--pair 用：裝置名稱")
    ap.add_argument("--event", choices=sorted(EVENT_FEATURE),
                    help="只送一次事件就結束，不進入常駐模式")
    ap.add_argument("--pair-only", action="store_true", help="只做配對，不進入常駐模式")
    args = ap.parse_args()

    if args.model not in models:
        sys.exit(f"models.yaml 裡沒有型號 {args.model}，可用的有：" + "、".join(models))
    model_def = models[args.model]

    dev = FakeDevice(args.mac, args.model, model_def, args.broker, args.port)

    if args.pair or args.pair_only:
        if not args.family or not args.owner:
            sys.exit("--pair 需要同時給 --family 與 --owner")
        if not secure_pair(args.api, args.mac, args.model, model_def,
                           args.family, args.owner, args.name):
            return 1
        if args.pair_only:
            return 0

    if args.event:
        # 一次性事件：連上、送出、離開
        need = EVENT_FEATURE[args.event]
        if not dev.has(need):
            sys.exit(f"{args.model} 的 features 沒有 {need}，送不出 {args.event}")
        c = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=f"esp32-evt-{args.mac}")
        c.connect(args.broker, args.port, keepalive=10)
        c.loop_start()
        payload = json.dumps({"type": args.event})
        c.publish(f"home/device/{args.mac}/event", payload).wait_for_publish(timeout=5)
        print(f"[fake {args.mac}] [EVENT] {payload} 已送出", flush=True)
        c.loop_stop(); c.disconnect()
        return 0

    dev.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
