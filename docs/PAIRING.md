# 裝置配對規格 v1（米家／Google Home 風格）

**版本**：v1.0　**日期**：2026-10-26　**對應決策**：Q1=3（免配對碼，靠實體按鈕＋短窗口）、Q2=4（先規格）

---

## 1. 目標

讓使用者**不必輸入 MAC** 就能把門鎖加入家庭：

1. 鎖進入配對模式（實體按鈕，指示燈閃爍）
2. App 自動發現「附近有 1 台裝置」
3. 點一下 → 命名／選房間 → 完成
4. 全程 ≤ 30 秒，錯誤有明確提示

**非目標**（v1 不做）：

- 藍牙配 Wi-Fi（本專案走有線／既有 Wi-Fi）
- 雲端帳號綁定（目前是自架，帳號只在自家 API）
- Matter／Thread 標準相容

---

## 2. 安全性前提（Q1=3 的已知代價）

**不做配對碼，代表唯一的「所有權證明」是「人就在裝置旁邊」。**

| 風險 | 說明 | v1 緩解 |
|---|---|---|
| 區網內搶配 | 窗口內任何人先 claim 就贏 | ① 必須**實體按鈕**才開窗（不能靠重開機自動開）② 窗口 **90 秒**，逾時自動關 ③ 同一時間只允許 **1 個**進行中的 claim |
| 假冒裝置 | 任何人可發 `home/pair/announce` | ① claim 後裝置必須回 `result` 才算成功 ② 未收到 result → 逾時失敗，不寫 DB |
| 重放 | 舊 announce 被重送 | pending 有 TTL 90 秒；`nonce` 由裝置每次重新產生 |
| 匿名 broker | `allow_anonymous true` 尚未關閉 | **列為 v2 前置條件**：配對完成後才發該 MAC 的 ACL 權限 |

> **明確聲明**：v1 在「同一區網 + 窗口開啟中」是可被搶配的。這是刻意的取捨（demo 優先），不可用於公開場域。升級路徑見 §9。

---

## 3. Topic 設計

| Topic | 方向 | Retain | QoS | Payload |
|---|---|---|---|---|
| `home/pair/announce` | 裝置 → 伺服器 | 否 | 0 | `{"mac","model","fw","nonce","ts"}` |
| `home/pair/<mac>/accept` | 伺服器 → 裝置 | **是** | 1 | `{"family_id","device_name","ts","expires_in","nonce"}` |
| `home/pair/<mac>/result` | 裝置 → 伺服器 | 否 | 1 | `{"ok":true,"mac","nonce"}` |
| `home/device/<mac>/cmd` | 伺服器 → 裝置 | 否 | 1 | **維持定向，不廣播** |

**規則**：廣播只用於「我還不知道你是誰」。指令與狀態一律定向。

---

## 4. HTTP API

掛在 `mqtt-server` 既有的 :8080。

| Method | Path | 用途 | 請求 | 回應 |
|---|---|---|---|---|
| GET | `/pair/pending` | 待配對清單 | — | `{"ttl_seconds":90,"devices":[{mac,model,fw,age,announces}]}` |
| POST | `/pair/accept/<mac>` | 送出配對確認 | `{"family_id","device_name"}` | `200 {"ok":true,"topic","payload"}` / `400` |
| POST | `/pair/cancel/<mac>` | 取消（使用者反悔／逾時） | — | `200 {"ok":true}` |

**API 層（:8091）**：配對成功後才寫 DB。

| Method | Path | 用途 |
|---|---|---|
| POST | `/device_claim` | `{family_id, mac, name, room}` → 呼叫 `/pair/accept` → 等 result → 寫 `devices` → 寫 ledger `DEVICE_REGISTERED_AND_PAIRED` |

---

## 5. 狀態機

```
        ┌──────────┐  實體按鈕    ┌───────────┐  App 發現   ┌──────────┐
        │  IDLE    │────────────▶│ ANNOUNCING│───────────▶│ PENDING  │
        └──────────┘  (長按 5s)   └───────────┘             └────┬─────┘
             ▲                         │ 90s 逾時               │ claim
             │                         ▼                        ▼
             │                    ┌─────────┐            ┌────────────┐
             └────────────────────│ TIMEOUT │            │  CLAIMING  │
                                  └─────────┘            └─────┬──────┘
                                                               │ result ok
                                                               ▼
                                                         ┌──────────┐
                                                         │  PAIRED  │
                                                         └──────────┘
```

| 狀態 | 裝置行為 | LED | App 顯示 |
|---|---|---|---|
| IDLE | 不廣播 | 熄滅 | 不出現 |
| ANNOUNCING | 每 3 秒 announce，最多 30 次 | 慢閃 | 「發現 1 台裝置」 |
| PENDING | 收到 accept 前持續 announce | 慢閃 | 卡片可點 |
| CLAIMING | 收到 accept → 存 NVS → 回 result | 快閃 | 「配對中…」 |
| PAIRED | 停止 announce，改走正常 cmd | 恆亮 3 秒後熄 | 進入裝置頁 |
| TIMEOUT | 停止 announce，回 IDLE | 熄滅 | 「已逾時，請重新長按」 |

---

## 6. 資料模型（MySQL `devices`）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `device_id` | VARCHAR | MAC（大寫、冒號分隔） |
| `family_id` | VARCHAR | 綁定的家庭 |
| `name` | VARCHAR | 使用者命名（預設 = 型號 + 末四碼） |
| `room` | VARCHAR NULL | 房間（可後改） |
| `pairing_status` | ENUM | `unpaired` / `paired` / `revoked` |
| `paired_at` | DATETIME | 配對時間 |
| `fw_version` | VARCHAR | 來自 announce |

**裝置端 NVS 儲存**：`family_id`、`device_name`（重開機不需重配）。

---

## 7. 錯誤碼

| 代碼 | 情境 | App 顯示 |
|---|---|---|
| `PAIR_TIMEOUT` | 90 秒內未完成 | 「裝置已停止配對，請在鎖上重新長按 5 秒」 |
| `PAIR_NOT_FOUND` | claim 的 MAC 不在 pending | 「找不到這台裝置，請重新掃描」 |
| `PAIR_BUSY` | 已有其他 claim 進行中 | 「這台裝置正在被配對」 |
| `PAIR_REJECTED` | 裝置回 `ok:false` | 「裝置拒絕配對，請重試」 |
| `PAIR_ALREADY_BOUND` | DB 已有其他 family | 「此裝置已屬於其他家庭」 |

---

## 8. 驗收清單

- [ ] 長按 5 秒 → LED 慢閃，App 5 秒內看到裝置
- [ ] 未長按時，App **看不到**任何裝置
- [ ] 90 秒未配 → LED 熄滅、清單自動消失
- [ ] 兩人同時 claim → 一人成功、一人收 `PAIR_BUSY`
- [ ] 配對成功 → DB `pairing_status='paired'`、ledger 有 `DEVICE_REGISTERED_AND_PAIRED`
- [ ] 重開機 → 不需重配，直接可被 API 控制
- [ ] 拔出／解除配對 → `revoked`，LED 需重新長按才能再配

---

## 9. 升級路徑（v2）

1. **配對碼／機身 QR**：每台鎖帶 `pair_secret`，claim 時驗證 → 消除搶配風險
2. **broker 帳密 + ACL**：`allow_anonymous false`，裝置只能發布自己的 topic
3. **配對紀錄通知**：任何人 claim 成功都推播給家庭管理員
4. **mDNS 發現**：App 不必連 broker 也能找到裝置（跨網段時尤其需要）

---

## 10. 已實作 / 待實作

| 項目 | 狀態 | 位置 |
|---|---|---|
| `home/pair/announce` 接收 + pending TTL | ✅ 已寫 | `mqtt-server/pairing.py` |
| `GET /pair/pending` | ✅ 已寫 | `mqtt-server/main.py` |
| `POST /pair/accept/<mac>` | ✅ 已寫 | `mqtt-server/main.py` |
| `POST /pair/cancel/<mac>` | ❌ 待做 | 同上 |
| TTL 90 秒（目前 180） | ❌ 待改 | `PENDING_TTL` |
| API `/device_claim` | ❌ 待做 | `API/` |
| 韌體：按鈕、LED、NVS、announce | ❌ 待做 | `Arduino/MqttSmartLock/MqttSmartLock.ino` |
| App 新增裝置頁 | ❌ 待做 | `app/lib/screens/add_device_screen.dart` |
| 語法檢查／部署 | ❌ 未驗（無 shell） | — |
