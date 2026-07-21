"""
MQTT IoT 網頁監控伺服器
- GET  /          → 監控頁面
- WS   /ws        → 即時推播裝置狀態與事件
- POST /api/cmd   → 發送 lock / unlock 指令
"""
import asyncio
import json
import time
import threading
from pathlib import Path

import uvicorn
import yaml
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

BROKER = "localhost"
DEVICES_FILE = Path("devices.json")
MODELS: dict = yaml.safe_load(open("models.yaml")) if Path("models.yaml").exists() else {}

# ── 共享狀態 ──────────────────────────────────────────────────────────────────
devices: dict = {}   # mac → {model, locked, online, last_seen}
event_log: list = [] # [{time, mac, type}]
ws_clients: set[WebSocket] = set()
main_loop: asyncio.AbstractEventLoop | None = None
mqtt_client: mqtt.Client | None = None


def _load_devices_file():
    if DEVICES_FILE.exists():
        try:
            saved = json.loads(DEVICES_FILE.read_text())
            for mac, info in saved.items():
                devices.setdefault(mac, {}).update(info)
                devices[mac]["online"] = False  # 重啟後視為離線直到狀態回報
                # 若 JSON 沒有 locked 欄位，從型號 default_state 推斷
                if "locked" not in devices[mac]:
                    model = info.get("model", "")
                    model_def = MODELS.get(model, {})
                    devices[mac]["locked"] = model_def.get("default_state") == "locked"
        except Exception:
            pass


# ── WebSocket 廣播 ────────────────────────────────────────────────────────────
async def _broadcast():
    dead = set()
    msg = json.dumps({
        "devices": {
            mac: {**d, "last_seen_str": _fmt_time(d.get("last_seen"))}
            for mac, d in devices.items()
        },
        "events": event_log[-30:],
    })
    for ws in ws_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    ws_clients.difference_update(dead)


def _schedule_broadcast():
    if main_loop:
        asyncio.run_coroutine_threadsafe(_broadcast(), main_loop)


def _fmt_time(ts):
    if not ts:
        return "-"
    return time.strftime("%H:%M:%S", time.localtime(ts))


# ── MQTT callbacks ────────────────────────────────────────────────────────────
def _on_connect(client, userdata, connect_flags, reason_code, properties):
    print(f"[MQTT] 連線成功 (rc={reason_code})")
    client.subscribe("home/register")
    client.subscribe("home/device/+/config")  # retained → 啟動時自動恢復已知裝置
    client.subscribe("home/device/+/state")
    client.subscribe("home/device/+/event")
    client.subscribe("home/device/+/cmd")     # 接收自己發出的指令以同步狀態


def _on_message(client, userdata, msg):
    parts = msg.topic.split("/")
    try:
        payload = json.loads(msg.payload.decode())
    except json.JSONDecodeError:
        return

    if len(parts) == 4 and parts[3] == "config":
        # Broker 在訂閱時自動推送 retained config，代表此裝置曾成功註冊
        mac = parts[2]
        default_state = payload.get("default_state", "")
        # 從 handler 名稱反查型號
        handler = payload.get("handler", "")
        model = next(
            (m for m, d in MODELS.items() if d.get("handler") == handler),
            devices.get(mac, {}).get("model", "unknown"),
        )
        devices.setdefault(mac, {})
        devices[mac].setdefault("locked", default_state == "locked")
        devices[mac].setdefault("model", model)
        devices[mac]["online"] = True   # retained config 存在即視為上線
        devices[mac].setdefault("last_seen", time.time())

    elif msg.topic == "home/register":
        mac = payload.get("mac", "")
        if not mac:
            return
        model = payload.get("model", "unknown")
        model_def = MODELS.get(model, {})
        # 若尚未有 locked 狀態，從型號 default_state 推斷初始值
        if "locked" not in devices.get(mac, {}):
            default_locked = model_def.get("default_state") == "locked"
            devices.setdefault(mac, {})["locked"] = default_locked
        devices[mac].update({
            "model": model,
            "online": True,
            "last_seen": time.time(),
        })

    elif len(parts) == 4 and parts[3] == "cmd":
        mac = parts[2]
        action = payload.get("action", "")
        if action in ("lock", "unlock"):
            devices.setdefault(mac, {})["locked"] = (action == "lock")

    elif len(parts) == 4 and parts[3] == "state":
        mac = parts[2]
        devices.setdefault(mac, {}).update({
            "locked": payload.get("locked"),
            "online": True,
            "last_seen": time.time(),
        })

    elif len(parts) == 4 and parts[3] == "event":
        mac = parts[2]
        event_type = payload.get("type", "unknown")
        event_log.append({
            "time": time.strftime("%H:%M:%S"),
            "mac": mac,
            "type": event_type,
        })
        if len(event_log) > 100:
            event_log.pop(0)

    _schedule_broadcast()


def _start_mqtt():
    global mqtt_client
    mqtt_client = mqtt.Client(CallbackAPIVersion.VERSION2)
    mqtt_client.on_connect = _on_connect
    mqtt_client.on_message = _on_message
    mqtt_client.connect(BROKER, 1883)
    mqtt_client.loop_forever()


# ── FastAPI app ───────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global main_loop
    main_loop = asyncio.get_running_loop()
    _load_devices_file()
    threading.Thread(target=_start_mqtt, daemon=True).start()
    yield


app = FastAPI(title="MQTT IoT Monitor", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    ws_clients.add(websocket)
    # 立即推播當前狀態
    await _broadcast()
    try:
        while True:
            await websocket.receive_text()  # 保持連線
    except WebSocketDisconnect:
        ws_clients.discard(websocket)


class CmdRequest(BaseModel):
    mac: str
    action: str  # "lock" | "unlock"


@app.post("/api/cmd")
async def send_cmd(req: CmdRequest):
    if req.action not in ("lock", "unlock"):
        return JSONResponse({"error": "invalid action"}, status_code=400)
    if mqtt_client is None:
        return JSONResponse({"error": "mqtt not ready"}, status_code=503)
    topic = f"home/device/{req.mac}/cmd"
    mqtt_client.publish(topic, json.dumps({"action": req.action}))
    print(f"[API] 發送指令 → {topic}: {req.action}")
    return {"ok": True}


@app.get("/api/devices")
async def get_devices():
    return devices


# ── 嵌入 HTML ─────────────────────────────────────────────────────────────────
HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MQTT IoT 監控</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0f1117; color: #e2e8f0; min-height: 100vh; }

  header {
    display: flex; align-items: center; gap: 12px;
    padding: 16px 24px; background: #161b27; border-bottom: 1px solid #2d3748;
  }
  header .dot { width: 10px; height: 10px; border-radius: 50%; background: #48bb78; box-shadow: 0 0 8px #48bb78; }
  header h1 { font-size: 1.1rem; font-weight: 600; letter-spacing: .5px; }
  header .sub { margin-left: auto; font-size: .75rem; color: #718096; }

  .layout { display: grid; grid-template-columns: 1fr 340px; gap: 20px; padding: 20px 24px; }
  @media (max-width: 800px) { .layout { grid-template-columns: 1fr; } }

  h2 { font-size: .8rem; text-transform: uppercase; letter-spacing: 1px; color: #718096; margin-bottom: 14px; }

  /* Device cards */
  .card {
    background: #161b27; border: 1px solid #2d3748; border-radius: 12px;
    padding: 18px; margin-bottom: 14px; transition: border-color .2s;
  }
  .card:hover { border-color: #4a5568; }
  .card-header { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
  .status-dot {
    width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0;
    transition: background .3s, box-shadow .3s;
  }
  .status-dot.online  { background: #48bb78; box-shadow: 0 0 7px #48bb78; }
  .status-dot.offline { background: #718096; }
  .card-mac  { font-size: .8rem; color: #a0aec0; font-family: monospace; }
  .card-model { font-size: .75rem; color: #4a5568; margin-left: auto; }

  .lock-badge {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 6px 14px; border-radius: 20px; font-size: .85rem; font-weight: 600;
    transition: background .3s, color .3s;
  }
  .lock-badge.locked   { background: #2d3748; color: #fc8181; }
  .lock-badge.unlocked { background: #1a3a2a; color: #68d391; }
  .lock-badge.unknown  { background: #2d3748; color: #718096; }

  .card-meta { font-size: .72rem; color: #4a5568; margin-top: 10px; }

  .btn-row { display: flex; gap: 8px; margin-top: 14px; }
  .btn {
    flex: 1; padding: 8px; border-radius: 8px; border: none; cursor: pointer;
    font-size: .8rem; font-weight: 600; transition: opacity .15s, transform .1s;
  }
  .btn:active { transform: scale(.96); }
  .btn:disabled { opacity: .35; cursor: not-allowed; }
  .btn-unlock { background: #276749; color: #c6f6d5; }
  .btn-unlock:not(:disabled):hover { background: #2f855a; }
  .btn-lock   { background: #742a2a; color: #fed7d7; }
  .btn-lock:not(:disabled):hover   { background: #9b2c2c; }

  /* Event log */
  .event-panel { background: #161b27; border: 1px solid #2d3748; border-radius: 12px; padding: 16px; }
  .event-list { max-height: 60vh; overflow-y: auto; }
  .event-item {
    display: flex; align-items: flex-start; gap: 10px;
    padding: 8px 0; border-bottom: 1px solid #1e2533; font-size: .8rem;
  }
  .event-item:last-child { border-bottom: none; }
  .event-time { color: #4a5568; flex-shrink: 0; font-family: monospace; }
  .event-mac  { color: #a0aec0; font-family: monospace; flex-shrink: 0; font-size: .72rem; }
  .event-type { font-weight: 600; }
  .event-type.doorbell       { color: #f6e05e; }
  .event-type.tamper_detected{ color: #fc8181; }
  .event-type.default        { color: #90cdf4; }

  .empty { color: #4a5568; font-size: .8rem; text-align: center; padding: 24px 0; }

  /* WS status */
  .ws-indicator { display: flex; align-items: center; gap: 6px; font-size: .7rem; color: #718096; }
  .ws-dot { width: 7px; height: 7px; border-radius: 50%; background: #718096; }
  .ws-dot.connected { background: #48bb78; box-shadow: 0 0 6px #48bb78; }

  /* Toast */
  .toast {
    position: fixed; bottom: 24px; right: 24px; z-index: 9999;
    padding: 10px 18px; border-radius: 8px; font-size: .85rem; font-weight: 600;
    animation: slidein .2s ease;
  }
  .toast-ok  { background: #276749; color: #c6f6d5; }
  .toast-err { background: #742a2a; color: #fed7d7; }
  @keyframes slidein { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:none; } }
</style>
</head>
<body>
<header>
  <div class="dot"></div>
  <h1>MQTT IoT 監控</h1>
  <div class="ws-indicator">
    <div class="ws-dot" id="wsDot"></div>
    <span id="wsStatus">連線中…</span>
  </div>
  <div class="sub" id="lastUpdate">-</div>
</header>

<div class="layout">
  <div>
    <h2>裝置狀態</h2>
    <div id="deviceList"><div class="empty">尚無裝置上線</div></div>
  </div>
  <div>
    <h2>事件記錄</h2>
    <div class="event-panel">
      <div class="event-list" id="eventList"><div class="empty">尚無事件</div></div>
    </div>
  </div>
</div>

<script>
const EVENT_LABELS = { doorbell: '🔔 門鈴', tamper_detected: '⚠️ 防拆警報' };

// 本地狀態：WS 更新與樂觀更新共用
let localState = { devices: {}, events: [] };

// ── 渲染 ──────────────────────────────────────────────────────────────────────
function render() {
  renderDevices(localState.devices);
  renderEvents(localState.events);
}

function renderDevices(devices) {
  const el = document.getElementById('deviceList');
  const macs = Object.keys(devices);
  if (!macs.length) { el.innerHTML = '<div class="empty">尚無裝置上線</div>'; return; }

  el.innerHTML = macs.map(mac => {
    const d = devices[mac];
    const online   = d.online;
    const locked   = d.locked;
    const badgeCls = locked === true ? 'locked' : locked === false ? 'unlocked' : 'unknown';
    const badgeTxt = locked === true ? '🔒 鎖上' : locked === false ? '🔓 開啟' : '— 未知';
    const sid      = mac.replace(/:/g, '');
    const offlineBadge = !online ? ' &nbsp;<span style="color:#fc8181;font-size:.7rem">離線</span>' : '';
    return `
<div class="card">
  <div class="card-header">
    <div class="status-dot ${online ? 'online' : 'offline'}"></div>
    <span class="card-mac">${mac}</span>
    <span class="card-model">${d.model || '-'}</span>
  </div>
  <span class="lock-badge ${badgeCls}">${badgeTxt}</span>${offlineBadge}
  <div class="card-meta">最後更新：${d.last_seen_str || '-'}</div>
  <div class="btn-row">
    <button id="btn-unlock-${sid}" class="btn btn-unlock" onclick="sendCmd('${mac}','unlock')">解鎖</button>
    <button id="btn-lock-${sid}"   class="btn btn-lock"   onclick="sendCmd('${mac}','lock')">上鎖</button>
  </div>
</div>`;
  }).join('');
}

function renderEvents(events) {
  const el = document.getElementById('eventList');
  if (!events.length) { el.innerHTML = '<div class="empty">尚無事件</div>'; return; }
  el.innerHTML = [...events].reverse().map(e => {
    const cls   = e.type in EVENT_LABELS ? e.type : 'default';
    const label = EVENT_LABELS[e.type] || e.type;
    return `
<div class="event-item">
  <span class="event-time">${e.time}</span>
  <span class="event-mac">${e.mac.slice(-5)}</span>
  <span class="event-type ${cls}">${label}</span>
</div>`;
  }).join('');
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function showToast(msg, ok = true) {
  const t = document.createElement('div');
  t.className = 'toast ' + (ok ? 'toast-ok' : 'toast-err');
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2500);
}

// ── 發送指令（含樂觀更新） ─────────────────────────────────────────────────────
async function sendCmd(mac, action) {
  const sid       = mac.replace(/:/g, '');
  const btnUnlock = document.getElementById(`btn-unlock-${sid}`);
  const btnLock   = document.getElementById(`btn-lock-${sid}`);

  // 按鈕 loading 狀態
  if (btnUnlock) btnUnlock.disabled = true;
  if (btnLock)   btnLock.disabled   = true;
  const target = action === 'unlock' ? btnUnlock : btnLock;
  if (target) target.textContent = '送出中…';

  try {
    const r = await fetch('/api/cmd', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mac, action }),
    });
    if (r.ok) {
      // 樂觀更新：不等裝置回報，先反映 UI
      if (localState.devices[mac]) {
        localState.devices[mac].locked = (action === 'lock');
      }
      render();
      showToast(action === 'unlock' ? '🔓 解鎖指令已送出' : '🔒 上鎖指令已送出');
    } else {
      const body = await r.text();
      showToast('指令失敗：' + body, false);
      render();
    }
  } catch (e) {
    showToast('網路錯誤：' + e.message, false);
    render();
  }
}

// ── WebSocket ─────────────────────────────────────────────────────────────────
function connectWS() {
  const ws        = new WebSocket(`ws://${location.host}/ws`);
  const dot       = document.getElementById('wsDot');
  const statusEl  = document.getElementById('wsStatus');

  ws.onopen = () => {
    dot.className  = 'ws-dot connected';
    statusEl.textContent = '已連線';
  };
  ws.onmessage = (e) => {
    const data = JSON.parse(e.data);
    // WS 資料為權威值，蓋過樂觀更新
    localState.devices = data.devices || {};
    localState.events  = data.events  || [];
    render();
    document.getElementById('lastUpdate').textContent =
      '更新：' + new Date().toLocaleTimeString('zh-Hant');
  };
  ws.onclose = () => {
    dot.className  = 'ws-dot';
    statusEl.textContent = '已斷線，重連中…';
    setTimeout(connectWS, 3000);
  };
  ws.onerror = () => ws.close();
}

connectWS();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
