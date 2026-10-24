"""外部推播（Telegram / ntfy），給 alert_service.py 用。

兩個通道各自用環境變數開關，沒設就略過，都沒設等於不推播：
- Telegram：TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
- ntfy：NTFY_TOPIC（NTFY_URL 預設 https://ntfy.sh；自架或私有 topic 可加 NTFY_TOKEN）

目前是「整個系統一個推播目的地」，不分家庭/使用者——資料庫沒有存使用者的
Telegram chat_id 或 ntfy topic。要做到個人化推播得先補 schema。
只用標準函式庫（urllib），不增加相依套件。
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import List

TIMEOUT_SEC = float(os.getenv("PUSH_TIMEOUT_SEC", "10"))

# severity -> ntfy priority（1~5）
NTFY_PRIORITY = {"LOW": "3", "MEDIUM": "3", "HIGH": "4", "CRITICAL": "5"}


def _post(url: str, data: bytes, headers: dict) -> int:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
        return resp.status


def send_telegram(title: str, message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    body = json.dumps({"chat_id": chat_id, "text": f"{title}\n{message}"}).encode("utf-8")
    _post(f"https://api.telegram.org/bot{token}/sendMessage", body,
          {"Content-Type": "application/json"})


def send_ntfy(title: str, message: str, severity: str) -> None:
    topic = os.getenv("NTFY_TOPIC")
    if not topic:
        return
    base = os.getenv("NTFY_URL", "https://ntfy.sh").rstrip("/")
    # ntfy 的 JSON 發布格式（POST 到根網址），標題才能放中文——
    # HTTP header 只能是 latin-1，用 Title header 傳中文會直接炸掉。
    body = {
        "topic": topic,
        "title": title,
        "message": message,
        "priority": int(NTFY_PRIORITY.get(severity, "3")),
    }
    headers = {"Content-Type": "application/json"}
    token = os.getenv("NTFY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    _post(base, json.dumps(body).encode("utf-8"), headers)


def send_push(title: str, message: str, severity: str = "HIGH") -> List[str]:
    """對所有已設定的通道推播，回傳失敗訊息清單（空清單＝全部成功或沒設定）。
    一個通道失敗不影響另一個。"""
    errors = []
    for name, fn in (("telegram", lambda: send_telegram(title, message)),
                     ("ntfy", lambda: send_ntfy(title, message, severity))):
        try:
            fn()
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    return errors


def push_configured() -> bool:
    return bool((os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))
                or os.getenv("NTFY_TOPIC"))
