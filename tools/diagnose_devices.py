#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""診斷「裝置存在卻讀不出來」。

這是本專案最常見的問題，而且幾乎都是同一個原因：**裝置註冊有兩套獨立系統，
彼此沒有任何自動同步。**

    ① 韌體側  ESP32 -> home/register -> mqtt-server/registry.py
              -> 寫進 mqtt-server/devices.json
              （web_monitor :8090 顯示的就是這份）

    ② App 側  App「配對裝置」-> POST /device_pair
              -> 寫進 MySQL 的 devices 資料表
              （App、/list_devices、/dashboard 讀的是這份）

只做 ① 的後果：裝置在 :8090 上看得到、也在發狀態，但 App 完全看不到它，
而且 bridge 會直接丟掉它的狀態回報：

    [bridge] <mac>: not paired (no row in devices table), skipping state update

解法不是重開機或重新燒錄，是**拿 ① 顯示的 MAC 去 App 做一次配對**。

用法：
    python tools/diagnose_devices.py                 # 全面檢查
    python tools/diagnose_devices.py --user admin001 # 再檢查該帳號看得到什麼
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Windows 主控台預設 cp950，印不了部分字元；統一轉成可輸出的形式。
sys.stdout.reconfigure(errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DEVICES_JSON = ROOT / "mqtt-server" / "devices.json"

# 容器名稱與密碼取自 docker-compose.yml，可用環境變數覆蓋。
MYSQL_CONTAINER = os.getenv("MYSQL_CONTAINER", "iot_mysql")
MYSQL_ROOT_PW = os.getenv("MYSQL_ROOT_PASSWORD", "devroot123")
MYSQL_DB = os.getenv("DB_NAME", "devicemanagement")

RETIRED = {"revoked", "retired", "decommissioned"}


def sql(query: str):
    """跑一段 SQL，回傳 list[list[str]]。用 docker exec，不需要本機裝 mysql client。"""
    proc = subprocess.run(
        ["docker", "exec", MYSQL_CONTAINER, "mysql",
         f"-uroot", f"-p{MYSQL_ROOT_PW}", MYSQL_DB, "-N", "-B", "-e", query],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        print(f"  x 查不到資料庫：{err.splitlines()[-1] if err else '未知錯誤'}")
        print(f"    （容器 {MYSQL_CONTAINER} 有在跑嗎？docker compose ps）")
        sys.exit(1)
    return [line.split("\t") for line in proc.stdout.splitlines() if line.strip()]


def load_mqtt_devices() -> dict:
    if not DEVICES_JSON.exists():
        return {}
    try:
        return json.loads(DEVICES_JSON.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  x 讀不到 {DEVICES_JSON}：{exc}")
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description="診斷裝置為什麼讀不出來")
    ap.add_argument("--user", help="順便檢查這個帳號在各場域看得到哪些裝置")
    args = ap.parse_args()

    mqtt_devices = load_mqtt_devices()
    db_rows = sql(
        "SELECT device_id, IFNULL(device_name,''), IFNULL(status,''), "
        "IFNULL(family_id,''), IFNULL(owner_user_id,'') FROM devices"
    )
    db = {r[0]: {"name": r[1], "status": r[2], "family_id": r[3], "owner": r[4]}
          for r in db_rows}

    print("=" * 68)
    print("裝置註冊狀態")
    print("=" * 68)
    print(f"  韌體註冊（devices.json）：{len(mqtt_devices)} 台")
    print(f"  資料庫（MySQL devices）：{len(db)} 台")

    both = sorted(set(mqtt_devices) & set(db))
    only_mqtt = sorted(set(mqtt_devices) - set(db))
    only_db = sorted(set(db) - set(mqtt_devices))

    print()
    print(f"[OK]   兩邊都有（真正接通）：{len(both)} 台")
    for mac in both:
        d = db[mac]
        flag = "  ! 已除役" if d["status"].lower() in RETIRED else ""
        print(f"   {mac:24} family={d['family_id']:<4} {d['status']}{flag}")

    print()
    print(f"[缺]   只在韌體側、資料庫沒有：{len(only_mqtt)} 台")
    if only_mqtt:
        print("   -> App 看不到它們，bridge 也會丟掉它們的狀態回報。")
        print("   -> 解法：拿下面的 MAC 到 App「配對裝置」做一次 /device_pair。")
        for mac in only_mqtt:
            print(f"   {mac:24} {mqtt_devices[mac].get('model', '?')}")

    print()
    print(f"[孤]   只在資料庫、韌體沒註冊過：{len(only_db)} 台")
    if only_db:
        print("   -> App 看得到卡片，但沒有實體裝置會收到指令（控制會逾時）。")
        print("   -> 多半是測試資料，或裝置還沒開機 / 連不上 broker。")
        for mac in only_db:
            d = db[mac]
            print(f"   {mac:24} family={d['family_id']:<4} {d['status']}  {d['name']}")

    if both and not only_mqtt and not only_db:
        print()
        print("  兩邊完全一致。")

    # ---- 常見誤用提醒 ----
    print()
    print("=" * 68)
    print("其他會造成「讀不出來」的原因")
    print("=" * 68)
    print("  1. 呼叫 /list_devices 時多送了 user_id")
    print("     list_devices.py 會把 user_id 當成 owner_user_id 用，SQL 變成")
    print("     WHERE owner_user_id = <你> AND family_id = <場域>。裝置的 owner 是")
    print("     當初配對它的 Admin，所以其他成員一律拿到空清單。")
    print("     -> 只送 family_id。（App 已經是這樣做）")
    print()
    print("  2. 帳號在該場域沒有角色")
    print("     /dashboard 會回「User has no role in this family.」。")
    print("     邀請還沒被接受時也算沒有角色。")
    print()
    print("  3. 看錯場域")
    print("     裝置綁在特定 family_id 上，切到別的場域當然看不到。")

    # ---- 指定帳號的可見性 ----
    if args.user:
        print()
        print("=" * 68)
        print(f"帳號 {args.user} 的可見範圍")
        print("=" * 68)
        rows = sql(
            "SELECT uf.family_id, IFNULL(f.family_name,''), uf.role "
            "FROM user_families uf LEFT JOIN families f ON f.id = uf.family_id "
            f"WHERE uf.user_id = '{args.user}'"
        )
        if not rows:
            print(f"  x {args.user} 不屬於任何場域 —— 這就是它什麼都看不到的原因。")
            print("    -> 請該場域的 Admin 發邀請，並且對方要「接受」才會生效。")
        else:
            for fid, fname, role in rows:
                mine = [m for m, d in db.items() if d["family_id"] == fid]
                live = [m for m in mine if m in mqtt_devices]
                revoked = [m for m in mine if db[m]["status"].lower() in RETIRED]
                note = "（Revoked，等同沒有權限）" if role.lower() == "revoked" else ""
                print(f"  場域 {fid} {fname}  角色={role} {note}")
                print(f"     資料庫 {len(mine)} 台，其中韌體也在線 {len(live)} 台，"
                      f"已除役 {len(revoked)} 台")
        pending = sql(
            # 資料表的主鍵欄位叫 id（API 回給 App 時才改名成 invitation_id）
            "SELECT id, family_id, IFNULL(status,'') FROM family_invitations "
            f"WHERE invitee_uid = '{args.user}' AND status = 'Pending'"
        )
        if pending:
            print()
            print(f"  i 有 {len(pending)} 張邀請還沒接受，接受前不算該場域的成員：")
            for inv_id, fid, st in pending:
                print(f"     邀請 #{inv_id} -> 場域 {fid}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
