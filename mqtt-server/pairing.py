"""配對（broadcast 探索）

設計原則
--------
1. **廣播只用於「探索」與「配對握手」**，指令一律走 home/device/<mac>/cmd。
   把 cmd 也廣播會讓區網內每台裝置都收到所有指令，且日後無法用 ACL 收斂權限。
2. 裝置只在「配對模式」廣播，其他時間保持安靜（避免持續暴露 MAC）。
3. pending 清單有 TTL，過期自動消失，不落地、不 retain。
4. accept 是**定向**的（home/pair/<mac>/accept），不是廣播——避免把 family_id 發給所有人。

Topic
-----
裝置 → 伺服器   home/pair/announce          {"mac","model","fw","nonce","ts"}
伺服器 → 裝置   home/pair/<mac>/accept      {"family_id","ts","expires_in","nonce"}
裝置 → 伺服器   home/pair/<mac>/result      {"ok":true,"mac","nonce"}
"""

import json
import os
import re
import time
import threading

PENDING_TTL = float(os.getenv("PAIR_PENDING_TTL", "90"))
_MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")

_lock = threading.Lock()
_pending = {}


def _norm_mac(value):
    mac = str(value or "").strip().upper().replace("-", ":")
    return mac if _MAC_RE.match(mac) else None


def _prune(now):
    for mac in [m for m, e in _pending.items() if now - e["last_seen"] > PENDING_TTL]:
        _pending.pop(mac, None)


def handle_announce(payload):
    """裝置廣播現身。只記錄，不回應。"""
    mac = _norm_mac(payload.get("mac"))
    if not mac:
        print(f"[PAIR] 忽略無效 MAC: {payload.get('mac')!r}")
        return None

    model = str(payload.get("model") or "UNKNOWN")
    nonce = str(payload.get("nonce") or "")
    now = time.time()

    with _lock:
        _prune(now)
        entry = _pending.get(mac)
        if entry is None:
            entry = {
                "mac": mac,
                "model": model,
                "fw": payload.get("fw"),
                "nonce": nonce,
                "first_seen": now,
                "announces": 0,
            }
            _pending[mac] = entry
            print(f"[PAIR] 發現待配對裝置: {mac} ({model})")
        entry["last_seen"] = now
        entry["announces"] = entry.get("announces", 0) + 1
        if nonce:
            entry["nonce"] = nonce
        return dict(entry)


def list_pending():
    now = time.time()
    with _lock:
        _prune(now)
        out = []
        for e in _pending.values():
            item = {k: v for k, v in e.items() if k != "nonce"}
            item["age"] = round(now - e["first_seen"], 1)
            out.append(item)
        out.sort(key=lambda x: x["age"])
        return out


def accept(client, mac, family_id, ttl_s=120):
    """對指定裝置發出定向配對確認（不廣播）。"""
    mac = _norm_mac(mac)
    if not mac:
        return {"ok": False, "error": "invalid_mac"}
    if not family_id:
        return {"ok": False, "error": "missing_family_id"}

    with _lock:
        entry = _pending.get(mac)
        nonce = entry.get("nonce") if entry else None

    payload = {
        "family_id": family_id,
        "ts": int(time.time()),
        "expires_in": int(ttl_s),
        "nonce": nonce,
    }
    topic = f"home/pair/{mac}/accept"
    # retain=True：裝置若正好在重連，連上就能收到
    client.publish(topic, json.dumps(payload), qos=1, retain=True)
    print(f"[PAIR] 已送出配對確認: {mac} → family={family_id}")
    return {"ok": True, "topic": topic, "payload": payload}


def cancel(client, mac):
    """使用者反悔或逾時：清掉 pending 與 retained accept。"""
    mac = _norm_mac(mac)
    if not mac:
        return {"ok": False, "error": "invalid_mac"}
    with _lock:
        existed = _pending.pop(mac, None) is not None
    client.publish(f"home/pair/{mac}/accept", "", qos=1, retain=True)
    print(f"[PAIR] 取消配對: {mac} (pending={existed})")
    return {"ok": True, "mac": mac, "was_pending": existed}


def handle_result(client, mac, payload):
    mac = _norm_mac(mac)
    if not mac:
        return
    ok = bool(payload.get("ok"))
    print(f"[PAIR] 裝置回報: {mac} ok={ok}")
    if ok:
        # 成功後清掉 retain，避免裝置下次重開又收到舊的 accept
        client.publish(f"home/pair/{mac}/accept", "", qos=1, retain=True)
        with _lock:
            _pending.pop(mac, None)
