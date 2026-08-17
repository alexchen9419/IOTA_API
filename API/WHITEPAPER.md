# API 白皮書：家庭 / 裝置管理後端

本文件是 `API/` 目錄的完整技術參考，涵蓋架構決策、資料模型、12 支端點的請求/回應合約、安全機制、與 `mqtt-server`／實體 ESP32 的整合方式，以及已知限制。使用案例對照請見 [`UC.md`](../UC.md)；MQTT/OTA/Arduino 端細節請見根目錄 [`README.md`](../README.md) 與 [`HANDOVER.md`](../HANDOVER.md)。

---

## 目錄

1. [系統定位](#1-系統定位)
2. [架構決策](#2-架構決策)
3. [服務拓樸](#3-服務拓樸)
4. [資料模型](#4-資料模型)
5. [端點總覽](#5-端點總覽)
6. [端點詳細規格](#6-端點詳細規格)
7. [MQTT 整合與 topic 轉譯](#7-mqtt-整合與-topic-轉譯)
8. [安全機制](#8-安全機制)
9. [稽核日誌與雜湊鏈](#9-稽核日誌與雜湊鏈)
10. [部署與環境變數](#10-部署與環境變數)
11. [已知限制與待辦](#11-已知限制與待辦)

---

## 1. 系統定位

`API/` 是一組獨立開發的 **CGI 風格腳本**，負責帳號、家庭（場域）、裝置配對、零信任遠端控制與儀表板查詢。它原本沒有任何部署設定——沒有 Dockerfile、沒有 `requirements.txt`、沒有 MySQL schema，也從未實際執行過。本文件描述的是「把它部署起來，並讓 App → API → MQTT → 真實 ESP32」這條路徑打通後的狀態。

整合時的核心約束：**不大量修改既有 12 支腳本的商業邏輯**，只在必要處加入 TLS 輔助模組（`mqtt_tls.py`）與一支新的橋接程式（`mqtt_topic_bridge.py`），其餘用部署層（Dockerfile、docker-compose、schema.sql、gateway.py）補齊「能跑起來」所需的東西。

## 2. 架構決策

### 2.1 CGI 語意保留，換一顆執行引擎

每支腳本都是傳統 CGI 程式：從 **stdin** 讀 JSON body，印出 `Status: <code>` header 再印出 JSON body，用 `sys.exit()` 結束。這個介面被完整保留，因為重寫 12 支腳本的商業邏輯風險遠高於重寫「怎麼呼叫它們」。

沒有選擇標準做法（Apache + `mod_cgi`），原因：

- Repo 其餘部分（`mqtt-server/`）全是 Python + FastAPI + Docker Compose，引入 Apache 是完全不同的技術棧。
- Python 內建的 `http.server.CGIHTTPRequestHandler` 在非 Unix fork 模式下無法正確轉譯 `Status:` header 成真正的 HTTP 狀態碼。

改用一支約 100 行的 **FastAPI CGI adapter**（[`gateway.py`](gateway.py)）：

- `run_cgi()`：用 `subprocess.run()` 把對應 `.py` 當子行程執行，傳入 stdin body、`REQUEST_METHOD`/`QUERY_STRING`/`CONTENT_LENGTH`/`CONTENT_TYPE` 環境變數（模擬真正 CGI 環境）。
- 解析子行程 stdout 的 header block（用 `\n\n` 或 `\r\n\r\n` 切開），把 `Status:` 轉成 HTTP 狀態碼、`Content-Type:` 轉成 response media type。
- 子行程沒有任何輸出（例如未預期的 crash）時，回傳 `502` 並把 stderr 放進 `detail`。
- `ROUTES` 字典把 12 個路由對應到腳本相對路徑與允許的 HTTP method；`list_devices` 額外允許 `GET`（query string 會原封不動地放進子行程的 `QUERY_STRING`）。
- `/healthz` 回傳 `{"status": "ok"}`，供 Docker healthcheck 或監控使用。

好處：純 Python、跟專案其餘部分風格一致、每個請求各自一個子行程（天然隔離，一支腳本 crash 不影響其他請求）。代價：每個請求都要重新啟動一次 Python 直譯器與資料庫連線，沒有連線池——這是 CGI 模型本身的特性，非本次整合引入的問題。

### 2.2 PYTHONPATH 而非重構目錄

多支腳本（`control_device.py`、`mqtt_topic_bridge.py` 等）需要 `import mqtt_tls`，但它們的 CGI 子行程 `cwd` 是自己的子目錄（例如 `control_device/`），而 `mqtt_tls.py` 放在 `API/` 頂層。沒有搬動任何檔案或改 import 路徑，而是在容器環境變數設定 `PYTHONPATH=/app`，讓 Python 直譯器不論 `cwd` 是哪個子目錄都能找到頂層模組。

### 2.3 Topic 橋接而非修改控制腳本

詳見[第 7 節](#7-mqtt-整合與-topic-轉譯)。

## 3. 服務拓樸

```
┌──────────┐    HTTP :8091    ┌────────────────┐   subprocess    ┌─────────────────┐
│  App/curl │ ───────────────▶│ api (gateway.py)│ ──────────────▶│ 12 支 CGI 腳本     │
└──────────┘                  └────────────────┘                 └────────┬────────┘
                                                                            │ SQL
                                                                   ┌────────▼────────┐
                                                                   │  mysql (3306)    │
                                                                   │  devicemanagement│
                                                                   └────────▲────────┘
                                                                            │ SQL
┌───────────────────┐  MQTT (home/+/device/+/cmd)   ┌────────────────────┐│
│ api-mqtt-bridge     │◀──────────────────────────────│ mqtt-broker :8883  ││
│ (mqtt_topic_bridge) │───────────────────────────────▶│ (mosquitto, TLS)   │┘
└──────────┬──────────┘  home/device/<mac>/cmd,state  └─────────▲──────────┘
           │ device_status_update.handle_status()                 │ home/device/<mac>/*
           └────────────────────────────────────────▶ (直接呼叫)   │
                                                              ┌─────┴─────┐
                                                              │ ESP32 韌體 │
                                                              └───────────┘
```

`docker-compose.yml` 定義的服務：`mqtt-broker`、`mqtt-server`（既有的 OTA/裝置註冊服務）、`node-red`、`mysql`（新增）、`api`（新增，跑 `gateway.py`）、`api-mqtt-bridge`（新增，跑 `mqtt_topic_bridge.py`，同一個 image 換 `command`）。

## 4. 資料模型

`API/schema.sql` 從 12 支腳本實際使用的 SQL 語句反推而成，由 `mysql` 容器透過 `/docker-entrypoint-initdb.d/` 自動載入。9 張表：

| 表 | 用途 | 關鍵欄位 |
|---|---|---|
| `users` | 全域帳號 | `user_id`(唯一)、`password_hash`(bcrypt)、`status` |
| `families` | 家庭/場域 | `family_name`、`admin_uid` |
| `user_families` | 使用者在特定家庭的角色 | `user_id`+`family_id`(唯一)、`role`、`start_time`/`end_time`/`max_uses`（訪客時效） |
| `family_invitations` | 邀請流程 | `inviter_uid`、`invitee_uid`、`role`、`status`(Pending/Accepted/Rejected) |
| `devices` | 裝置主檔 | `device_id`(PK)、`family_id`、`device_public_key`/`gateway_public_key`/`session_key_hash`（UC2.1 配對）、`revoked_at`/`revoked_by`/`revocation_reason`（UC2.3 除役）、`physical_state`/`battery`/`rssi`/`online_status`（即時狀態）、`maintenance_mode`/`maintenance_expires_at`/`maintenance_reason`（UC5.1 維修模式） |
| `audit_logs` | 雜湊鏈稽核紀錄 | `command_id`、`action`、`parameters`(JSON)、`prev_hash`/`current_hash`/`hash`、`timestamp` |
| `guest_tokens` | 訪客短效令牌 | `token_id`(PK)、`token_hash`(SHA-256，非明文)、`expires_at`、`used_count`/`max_uses`、`revoked` |
| `control_commands` | 控制指令紀錄 | `command_id`(PK)、`control_mode`(mock/mqtt)、`target_topic`、`status`、`published_at`/`completed_at` |
| `device_telemetry` | 裝置回報歷史 | `device_id`、`physical_state`、`battery`/`rssi`、`recorded_at` |
| `policy_rules` | 零信任規則（可選） | `family_id`/`device_id`/`role`/`action`/`effect`(allow/deny)/`enabled` |

> **已知缺口**：schema 裡沒有任何 endpoint 能建立 `families` 資料列——測試環境目前靠手動 `INSERT INTO families`。
>
> **既有開發用資料庫的遷移**：`schema.sql` 只會在 `mysql` 容器第一次啟動、資料目錄為空時被 `docker-entrypoint-initdb.d` 自動執行；若 `./mysql-data` 已經有資料（開發過程中已經跑過的環境），新增的 `devices.maintenance_mode`/`maintenance_expires_at`/`maintenance_reason` 三個欄位不會自動補上，需要手動執行一次：
>
> ```sql
> ALTER TABLE devices
>   ADD COLUMN maintenance_mode TINYINT(1) NOT NULL DEFAULT 0,
>   ADD COLUMN maintenance_expires_at DATETIME NULL,
>   ADD COLUMN maintenance_reason VARCHAR(255);
> ```
>
> 或直接 `docker compose down` 後刪除 `./mysql-data` 重建（會清空既有測試資料）。

`control_device.py`、`device_status_update.py`、`get_family_dashboard.py` 這幾支較新的腳本對欄位名稱做了防禦性設計（`get_columns()`/`filter_by_columns()`/`select_first_by_any_column()`），可以容忍表格缺少某些欄位或用不同的候選欄位名（例如 `family_id`/`home_id`/`house_id` 皆可），因此即使 schema 之後演進也不容易整支炸掉。

## 5. 端點總覽

所有端點由 [`gateway.py`](gateway.py) 的 `ROUTES` 掛載在 `api` 容器的 `:8000`（對外映射 `8091`）。Base URL 範例：`http://localhost:8091`。

| 路由 | 對應腳本 | Method | 對應 UC | 說明 |
|---|---|---|:-:|---|
| `/register` | `register/register.py` | POST | UC1.1 | 建立帳號（bcrypt 雜湊密碼） |
| `/login` | `login/login.py` | POST | UC1.2 | 驗證密碼，回傳使用者所屬家庭清單（**無 Token 核發**） |
| `/send_invitation` | `send_invitation/send_invitation.py` | POST | UC3.2 | Admin 邀請已註冊帳號加入家庭 |
| `/respond_invitation` | `respond_invitation/respond_invitation.py` | POST | UC3.2 | 受邀者接受/拒絕邀請；接受時寫入 `user_families` 並發 MQTT 通知 |
| `/update_member_role` | `update_member_role/update_member_role.py` | POST | UC3.3 | 變更/撤銷家庭成員角色（Upsert，含 Revoked） |
| `/generate_guest_qr` | `generate_guest_qr/generate_guest_qr.py` | POST | UC3.4 | 產生訪客帳號＋隨機密碼＋QR 用 URL |
| `/device_pair` | `device_pair/device_pair.py` | POST | UC2.1 | ECDH 配對、HKDF 派生 session key、寫入 `devices`+`audit_logs` |
| `/list_devices` | `list_devices/list_devices.py` | GET/POST | UC2.1 | 查詢裝置清單與最近註冊紀錄 |
| `/decommission_device` | `decommission_device/decommission_device.py` | POST | UC2.3 | 除役裝置（**不含身分驗證**，見第 11 節） |
| `/ota_update` | `ota_update/ota_update.py` | POST | UC2.2 | Admin 觸發指定裝置的簽章韌體更新（MQTT OTA） |
| `/maintenance_mode` | `maintenance_mode/maintenance_mode.py` | GET/POST | UC5.1 | Admin 開啟/關閉裝置維修模式（強制設定最長有效時間，到期自動恢復） |
| `/control_device` | `control_device/control_device.py` | POST | UC4.1/UC4.2 | 零信任驗證後下發控制指令（mock 或 mqtt 模式，維修模式中會拒絕） |
| `/device_status_update` | `control_device/device_status_update.py` | POST | UC4.1/UC4.2 | 裝置執行結果回報（HTTP callback 版，MQTT 版走 bridge 直接呼叫同一函式） |
| `/dashboard` | `dashboard/get_family_dashboard.py` | POST | UC4.3 | 場域儀表板：裝置清單＋最新狀態＋連線健康度 |

不掛在 `gateway.py` 路由表、僅供測試使用的輔助腳本：

| 腳本 | 用途 |
|---|---|
| `control_device/issue_guest_token_demo.py` | UC4.2 測試用：手動核發 `guest_tokens` 明文令牌（正式的 UC3.4 核發流程尚未接上這張表） |
| `control_device/mqtt_status_worker.py` | `control_device.py` 假設的 `home/{family_id}/device/{device_id}/status` 慣例的訂閱者；**在本專案的實際 MQTT 拓樸下未被使用**，已被 `mqtt_topic_bridge.py` 取代（見第 7 節） |

## 6. 端點詳細規格

所有 POST 端點的請求格式一致：`{"payload": {...}}`。回應格式一致：`{"status": "Success"|"Error"|"Warning", ...}`，部分較新腳本額外帶 `"code"`（機器可讀錯誤碼）。

### 6.1 `/register`（UC1.1）

```json
POST /register
{"payload": {"user_id": "admin001", "username": "Admin", "password": "test1234",
             "email": "a@test.com", "phone_number": "0900000000"}}
```

- 201 通常回傳 `200`（腳本固定用 200，非 REST 慣例的 201）；成功時回傳 `{id, user_id, username, email, phone_number, status:"Active"}`。
- `409`：`user_id` 已存在（MySQL `IntegrityError` 1062）。
- `400`：欄位不齊全。

### 6.2 `/login`（UC1.2）

```json
POST /login
{"payload": {"user_id": "admin001", "password": "test1234"}}
```

- 成功回傳 `{user_id, username, status, families: [{family_id, family_name, user_role}, ...]}`。
- `401`：帳號不存在或密碼錯（用同一個錯誤訊息，避免帳號枚舉）。
- `403`：全域帳號 `status != 'Active'`（已被停用）。
- **不核發 Token**：後續所有 API 呼叫都是靠 payload 裡明文 `user_id` 辨識身分，見第 11 節。

### 6.3 `/send_invitation`（UC3.2）

```json
POST /send_invitation
{"payload": {"family_id": 1, "admin_uid": "admin001", "invitee_uid": "member01", "role": "Guest"}}
```

驗證順序：發起者必須是該 `family_id` 的 `admin_uid`（403）→ 受邀帳號必須存在（404）→ 不可重複邀請已是成員的人（409）→ 不可對同一人重複發送 Pending 邀請（409）→ 寫入 `family_invitations`，回傳 `201`。

### 6.4 `/respond_invitation`（UC3.2）

```json
POST /respond_invitation
{"payload": {"invitation_id": 1, "user_id": "member01", "action": "Accept"}}
```

- `action` 僅接受 `Accept`/`Reject`。
- 邀請必須存在、屬於該 `user_id`、且狀態為 `Pending`（否則 404/409）。
- `Accept` 時：更新邀請狀態 → `INSERT INTO user_families` → **交易 commit 後**才嘗試發 MQTT 通知（`home/security/gateway_{family_id}/auth_sync`，`{"event":"MEMBER_ADDED", ...}`）；MQTT 發送失敗不影響已完成的資料庫異動（`try/except: pass`）。
- 目前這個 topic **沒有任何服務訂閱**（見第 7 節、第 11 節），MQTT 通知等同無效果，但不影響資料庫層面的邀請流程本身。

### 6.5 `/update_member_role`（UC3.3）

```json
POST /update_member_role
{"payload": {"family_id": 1, "admin_uid": "admin001", "target_uid": "member01",
             "target_role": "Revoked", "start_time": null, "end_time": null, "max_uses": null}}
```

- 僅接受 POST（其他 method 405）。
- `target_role` 必須是 `Admin`/`Member`/`Guest`/`Technician`/`SP`/`Revoked` 之一。
- 禁止 Admin 對自己執行非 Admin 的角色變更（避免場域無人管理）。
- 用一條 SQL 同時查操作者與目標者的角色（`role_map`），確認操作者是 `Admin` 才放行（403）；目標帳號必須存在於 `users` 且全域 `status=Active`（404/403）。
- `target_role=Revoked`：Upsert 進 `user_families`，`end_time=NOW()`。
- 其他角色：Upsert（`ON DUPLICATE KEY UPDATE`），可同時設定臨時權限的 `start_time`/`end_time`/`max_uses`。
- 成功後用 `paho.mqtt.publish.single()` 非阻塞廣播到 `home/security/gateway_{family_id}/auth_sync`（同樣是無訂閱者的 topic，失敗不影響回應）。

### 6.6 `/generate_guest_qr`（UC3.4）

```json
POST /generate_guest_qr
{"payload": {"family_id": 1, "admin_uid": "admin001",
             "start_time": null, "end_time": "2026-08-01 00:00:00", "max_uses": 5}}
```

- 僅 Admin 可呼叫（查 `user_families.role`）。
- 兩種策略：優先重用「已撤銷或已過期」的 `guest_%` 帳號（重設密碼＋角色＋時效）；否則建立全新 `guest_{隨機6碼}` 帳號。
- 回傳明文密碼與 `control_url`（`https://your-domain.com/qr-control?uid=...&pwd=...`——**佔位網域，非真實部署位置**）。
- 這是走 `user_families`（帳號式）的訪客機制，跟 `guest_tokens` 表（`issue_guest_token_demo.py` 用的令牌式）是兩套不相關的實作，詳見第 11 節。

### 6.7 `/device_pair`（UC2.1）

```json
POST /device_pair
{"payload": {"owner_user_id": "admin001", "family_id": 1, "gateway_id": "GW_001",
             "device_id": "E8:31:CD:82:80:C8", "device_name": "客廳門鎖",
             "device_type": "smart_lock", "device_public_key_pem": null}}
```

- `device_public_key_pem` 可省略；省略時 API 會自行模擬一把 ESP32 ECDH 金鑰（`simulated_device: true`），方便在沒有真實裝置時測通全流程，且會反向驗證雙方算出的 session key 是否一致（`device_side_verified`）。
- 流程：Gateway 產生 `SECP256R1` 金鑰對 → 與（真實或模擬的）裝置 public key 做 ECDH → shared secret 經 `HKDF-SHA256`（info 含 `gateway_id`/`device_id`）派生 32 bytes session key → 只儲存 `session_key_hash`（SHA-256），**不儲存明文 session key**。
- 若該 `device_id` 已被 UC2.3 標記為 `revoked`/`retired`/`decommissioned`，拒絕重新配對（409），必須先有正式的重新啟用流程（目前未實作）。
- 成功時 Upsert `devices` 表、寫入 `audit_logs`（`DEVICE_REGISTERED`，含 hash chain），回傳 `session_key_hash`、`device_public_key_hash`、`gateway_public_key_hash` 與完整 `ledger`（`command_id`/`prev_hash`/`current_hash`）。

### 6.8 `/list_devices`（UC2.1 查詢）

```
GET /list_devices?owner_user_id=admin001&family_id=1
POST /list_devices  {"payload": {"owner_user_id": "admin001", "family_id": 1}}
```

- `owner_user_id`/`family_id` 皆可省略（省略即查全部，上限 100 筆裝置＋20 筆日誌）。
- 回傳 `data.devices`（`device_id`/`status`/`pairing_status`/`session_key_hash`/除役欄位等）與 `data.logs`（最近 20 筆 `DEVICE_REGISTERED` 稽核紀錄）。

### 6.9 `/decommission_device`（UC2.3）

```json
POST /decommission_device
{"payload": {"device_id": "E8:31:CD:82:80:C8", "reason": "汰換舊設備", "operator_user_id": "admin001"}}
```

- **檔案開頭自行註明「不含身分驗證版」**——`operator_user_id` 只被記錄進稽核日誌，不做任何權限檢查，任何呼叫者都能除役任意裝置。
- 用 `SELECT ... FOR UPDATE` 鎖定該裝置列，避免併發除役的競態。
- 若裝置已經是 `revoked`/`retired`/`decommissioned` 狀態，回傳成功但標記為 no-op（`UC2.3_DEVICE_DECOMMISSION_NOOP`），不重複寫入。
- 成功時：`status='Revoked'`、`pairing_status='unpaired'`、`session_key_hash=NULL`、`revoked_at=NOW()`、`revoked_by`/`revocation_reason` 寫入，並（若表存在）補一筆 `device_telemetry`。
- 對 `devices`/`audit_logs`/`device_telemetry` 的欄位存在性都做了防禦性檢查（`table_exists`/`get_columns`），可以在較舊/較精簡的 schema 上運作而不整支炸掉。

### 6.10 `/ota_update`（UC2.2）

```json
POST /ota_update
{"payload": {"family_id": 1, "admin_uid": "admin001", "device_id": "E8:31:CD:82:80:C8",
             "firmware_file": "SMART-LOCK-V1_1.1.0.bin", "version": "1.1.0"}}
```

- 這是前端觸發 OTA 的入口，取代原本只能在 `mqtt-server` 容器內手動跑 `test_ota.py` 的方式；發布出去的 MQTT 訊息格式跟 `test_ota.py` 完全一致，裝置端行為不變。
- **僅 Admin 可觸發**：查 `user_families` 確認 `admin_uid` 在該 `family_id` 的角色是 `Admin`，否則 403。這是本次新增時特意決定的權限模型（見下方說明）。
- 裝置驗證：`device_id` 必須存在、若已綁定 `family_id` 則必須與請求的 `family_id` 一致（403）、且未被除役（409）。
- 組出下載網址 `http://{OTA_HOST}:8080/firmware/{firmware_file}` 並發布到 `home/device/{device_id}/ota`，`payload={"url":..., "version":...}`——`OTA_HOST` 是環境變數（`docker-compose.yml` 的 `api` 服務），必須是 ESP32 能直接連到的區網 IP（容器內部位址對實體裝置沒用），跟 Arduino sketch 的 `MQTT_BROKER`、`mqtt-server/test_ota.py` 的 `OTA_HOST` 應該是同一台機器。
- **不檢查韌體檔案是否存在**：`firmware_file` 只做 `.bin` 副檔名檢查，不會去確認該檔案（與對應的 `.bin.sig`）真的放在 `mqtt-server/firmware/` 底下——若檔名打錯或忘記簽章，裝置端下載/驗證會失敗，但這支 API 本身仍回應「已觸發」成功。
- **不檢查版本**：`version` 純粹是紀錄用欄位，不會拿裝置目前版本比對，也不會阻擋重複推送同一版本或版本倒退。
- MQTT 發布失敗（broker 連不上等）會回傳 `502` 並仍寫入一筆 `status=Failed` 的稽核紀錄；發布成功則更新 `devices.last_action='OTA_TRIGGERED'` 並寫入 `action=OTA_TRIGGERED` 的稽核紀錄（含 hash chain）。
- **為何選擇「要驗證 Admin 身分」而非單純轉發**：其餘所有會改變裝置狀態的端點（`control_device`、`decommission_device` 除外）都至少檢查角色，讓 OTA 觸發沒有身分檢查會是這批端點裡唯一的例外，且韌體更新的風險（可能讓裝置變磚、或被用來推送惡意韌體——雖然 Ed25519 簽章會擋下未簽章/竄改的檔案）比一般的 lock/unlock 控制更高，因此採用跟 `update_member_role`/`generate_guest_qr` 一致的 Admin 檢查模式。這也代表 UC2.2 的觸發路徑重新變回「Admin 下發」，需要同步回頭調整 [`UC.md`](../UC.md) 的描述文字。

### 6.11 `/maintenance_mode`（UC5.1）

```json
POST /maintenance_mode
{"payload": {"family_id": 1, "admin_uid": "admin001", "device_id": "E8:31:CD:82:80:C8",
             "action": "Enable", "duration_minutes": 60, "reason": "更換電池"}}
```

```text
GET /maintenance_mode?device_id=E8:31:CD:82:80:C8
```

- **僅 Admin 可切換**：跟 `ota_update.py` 一樣查 `user_families` 角色（403）；裝置必須存在、屬於該家庭、未除役（403/404/409）。
- **開啟（`action=Enable`）強制要求 `duration_minutes`**：不可省略、必須是正整數、且不可超過 `MAX_MAINTENANCE_MINUTES`（環境變數，預設 240 分鐘）——這是 UC5.1 描述裡「系統強制要求設定最長有效時間」的具體實作，超過上限或缺漏一律 `400`。成功時把 `devices.maintenance_mode=1`、`maintenance_expires_at=NOW()+duration_minutes`、`maintenance_reason` 寫入，並記一筆 `MAINTENANCE_MODE_ENABLED` 稽核紀錄。
- **關閉（`action=Disable`）**：手動提前結束維修模式，清空三個欄位；若裝置本來就不在維修模式，回傳 `Warning`（no-op，不重複寫入）。
- **查詢（`GET` 或 `action=Status`）**：回傳目前 `maintenance_mode`/`maintenance_expires_at`/`maintenance_reason`；查詢時若已經過期會**順手自動清除**（等同讓 sweep 提前跑一次），所以查詢本身也是這個功能自我修復的一部分。
- **真正的自動恢復**由 `api-mqtt-bridge`（`mqtt_topic_bridge.py`）的背景執行緒負責，見第 7.4 節——不是靠這支 API 被呼叫才觸發，即使沒有人再打 `/maintenance_mode` 或 `/dashboard`，維修模式一樣會準時解除。
- **不涉及硬體診斷埠**：UC5.1 原始描述提到「對外開放受限的硬體診斷埠」，這是韌體/硬體層面的工作（例如 ESP32 開一個限定存取的除錯介面），需要實體裝置才能設計與驗證，本次沒有實作，也不在這支端點的職責內。
- **不支援「依設定條件觸發」**：目前只有 Admin 手動呼叫這個 endpoint 才會進入維修模式，沒有規則引擎能根據條件（例如電量過低、感測器異常）自動觸發。

### 6.12 `/control_device`（UC4.1 / UC4.2）

```json
POST /control_device
{"payload": {"family_id": 1, "device_id": "E8:31:CD:82:80:C8", "action": "UNLOCK",
             "auth_type": "user", "user_id": "admin001", "parameters": {}}}
```

或訪客模式：`"auth_type": "guest", "guest_token": "GUEST_xxx"`（取代 `user_id`）。

**驗證流程（`handle_control`）**：

1. `validate_device_scope`：裝置必須存在、屬於這個 `family_id`、且未被除役/停用（403/404/409）。
2. `check_maintenance_mode`（UC5.1）：若 `devices.maintenance_mode=1` 且未過期，拒絕（`409 DEVICE_IN_MAINTENANCE`）；若已過期（`maintenance_expires_at` 已過但 `api-mqtt-bridge` 的背景 sweep 還沒跑到），這裡會**順手自動清除**再放行，不會因為 sweep 還沒來得及跑而誤擋本來已經合法的指令。
3. `auth_type=user`：查使用者在該家庭的角色（`user_families`），只有 `admin`/`owner`/`member` 可執行（403）；接著跑 `evaluate_policy()` 讀 `policy_rules`（若無此表或無相符規則，預設放行——**零信任引擎在，但沒有任何 endpoint 能寫入規則**，見第 11 節）。
4. `auth_type=guest_token`：驗證 `guest_token` 的 SHA-256 雜湊、`revoked`、家庭/裝置範圍、`expires_at`、`used_count < max_uses`、`allowed_actions` 是否包含此 action（403 系列，逐項檢查）。
5. 通過後寫入 `control_commands`（`status=ACCEPTED`），呼叫對應的 `ControlAdapter`：
   - **`mock` 模式**（預設，`CONTROL_MODE` 未設定或設為 `mock`）：`MockControlAdapter` 直接假造裝置回應（`STATE_AFTER_ACTION` 對照表），立即更新 `devices` 影子狀態與 `device_telemetry`。
   - **`mqtt` 模式**（本專案 docker-compose 設定值）：`MqttControlAdapter` 發布到 `home/{family_id}/device/{device_id}/cmd`（**注意：這是 API 原本假設的 topic 慣例，跟真實 ESP32 韌體不同**，發布出去後由 `mqtt_topic_bridge.py` 轉譯成韌體聽得懂的格式，見第 7 節）。此模式下回應是 `PUBLISHED`（已發布，等待裝置非同步回報），不會立即更新裝置影子狀態。
6. Guest 模式成功執行後會 `consume_guest_token()`（`used_count += 1`）。
7. 不論成功/拒絕都寫入 `audit_logs`（`action=CONTROL_DEVICE`，`decision=ALLOW/DENY`）。

`ALLOWED_ACTIONS` 支援 `LOCK`/`UNLOCK`/`ON`/`OFF`/`OPEN`/`CLOSE`/`TOGGLE`/`START`/`STOP`，但實體 ESP32 韌體目前只認得 `lock`/`unlock`（小寫）——這個落差由 bridge 的 `ACTION_MAP` 處理，非此檔案支援範圍以外的動作會被 bridge 直接丟棄並記 log。

### 6.13 `/device_status_update`（UC4.1 / UC4.2 狀態回報）

```json
POST /device_status_update
{"payload": {"command_id": "CMD_...", "family_id": 1, "device_id": "E8:31:CD:82:80:C8",
             "status": "SUCCEEDED", "physical_state": "UNLOCKED", "battery": 87, "rssi": -52}}
```

- 設計給 `CONTROL_MODE=mqtt` 時，Gateway/Worker 收到裝置回報後呼叫此 HTTP 端點寫回資料庫（更新 `control_commands`、`devices` 影子狀態、新增 `device_telemetry`、寫 `audit_logs`）。
- **本專案實際上不透過 HTTP 呼叫這支端點**——`mqtt_topic_bridge.py` 直接 `import` 並呼叫 `handle_status()` 函式本體，略過 HTTP 這一層（見第 7 節）。這支端點本身仍然存在且可獨立測試，只是目前的 MQTT 拓樸沒有經過它的 HTTP 介面。

### 6.14 `/dashboard`（UC4.3）

```json
POST /dashboard
{"payload": {"auth_type": "user", "user_id": "admin001", "family_id": 1,
             "include_history": true, "history_limit": 5}}
```

- 僅 `user_id`（Admin/Member）可查，Guest token 一律拒絕（403，`GUEST_DASHBOARD_DENIED`）。
- 回傳該家庭所有裝置卡片：`physical_state`/`battery`/`rssi`/`connection_health`（`GOOD`/`WEAK`/`OFFLINE`/`NO_DATA`/`FAULT`/`UNKNOWN`，依 `rssi`、最後回報時間與 `battery` 門檻值計算）/`low_battery`/最近一筆控制指令。
- `include_history=true` 時每個裝置卡片額外帶最近 N 筆（上限 20）`device_telemetry` 歷史紀錄。
- 每次查詢（含被拒絕的）都寫入 `audit_logs`（`action=DASHBOARD_VIEWED`）。

## 7. MQTT 整合與 topic 轉譯

探索階段發現程式碼裡實際存在 **三種互不相容的 MQTT topic 慣例**：

| 來源 | Topic | 狀態 |
|---|---|---|
| `mqtt-server/`（已用真實 ESP32 驗證過） | `home/device/<mac>/cmd`\|`state`\|`event` | **唯一實測過的慣例** |
| `API/control_device/control_device.py`（`CONTROL_MODE=mqtt`）、`mqtt_status_worker.py` | `home/{family_id}/device/{device_id}/cmd`\|`status` | 從沒接過真裝置 |
| `API/respond_invitation.py`、`generate_guest_qr.py`、`update_member_role.py` | `home/security/gateway_{family_id}/auth_sync` | 通知家庭成員異動用，**沒有任何 subscriber**，跟裝置控制無關 |

**決策**：以 `mqtt-server` 已驗證的 topic/payload 為準，寫一支獨立的橋接程式 [`control_device/mqtt_topic_bridge.py`](control_device/mqtt_topic_bridge.py) 做雙向翻譯，而不是回頭改 `control_device.py`/`mqtt_status_worker.py` 的 topic 邏輯（改了也不會影響 Arduino 韌體端，且會讓兩套從沒被驗證過關係的程式碼更難單獨追蹤）。第三種（`home/security/gateway_*`）維持不動——是死代碼，不影響裝置控制主線。

### 7.1 命令方向：API → 韌體

```
control_device.py 發布  home/{family_id}/device/{device_id}/cmd   {"action": "UNLOCK", ...其他欄位}
                                    │
                                    ▼  bridge 訂閱 home/+/device/+/cmd
                        ACTION_MAP = {"LOCK": "lock", "UNLOCK": "unlock"}
                        （其他動作：記 log 後丟棄，不轉發）
                                    │
                                    ▼  bridge 發布
                          home/device/{device_id}/cmd   {"action": "unlock"}
                                    │
                                    ▼
                        Arduino 韌體（indexOf("\"unlock\"") 字串比對）
```

### 7.2 狀態方向：韌體 → API

```
Arduino 韌體發布  home/device/<mac>/state   {"locked": true|false}
                          │
                          ▼  bridge 訂閱 home/device/+/state
              lookup_family_id(mac)：查 devices 表拿 family_id
              （查不到＝裝置還沒被 UC2.1 配對過，記 log 後跳過，不硬塞假資料）
                          │
                          ▼  bridge 直接呼叫（非 HTTP，直接 import 函式）
    device_status_update.handle_status({family_id, device_id, status:"SUCCEEDED",
                                         physical_state: "LOCKED"|"UNLOCKED"})
```

```
Arduino 韌體發布  home/device/<mac>/event   {"type": "doorbell"|"tamper_detected"}
                          │
                          ▼  bridge 訂閱 home/device/+/event
              write_event_audit()：直接寫一筆 audit_logs
              （action=DEVICE_EVENT，沿用既有 hash-chain 手法，沒有新建資料表）
```

### 7.3 為什麼直接呼叫函式而非再發一次 MQTT

`device_status_update.py` 原本設計成一支獨立 CGI/HTTP 端點，給「Gateway worker 收到裝置回報後呼叫 HTTP API」這種架構用。但 bridge 跟 `device_status_update.py` 現在同屬一個 Docker image、同一份程式碼，多繞一層 HTTP 沒有實質好處，反而多一個網路失敗點，所以 bridge 選擇 `import device_status_update` 後直接呼叫 `handle_status()` 函式本體。`device_status_update.py` 本身的邏輯完全沒被修改。

### 7.4 維修模式背景 sweep（UC5.1）

`mqtt_topic_bridge.py`（`api-mqtt-bridge` 服務）是這個系統裡唯一長駐、不隨請求結束就消失的 API 程式碼，因此把 UC5.1「時間到達後自動恢復」的計時器放在這裡，而不是另外引入 cron 或排程套件：

```
main() 啟動時額外開一條背景執行緒（daemon thread）
        │
        ▼  每 MAINTENANCE_SWEEP_INTERVAL_SECONDS 秒（預設 60 秒）跑一次
  sweep_expired_maintenance()
        │
        ▼  SELECT device_id, family_id FROM devices
           WHERE maintenance_mode=1 AND maintenance_expires_at <= NOW()
        │
        ▼  對每一筆：UPDATE devices SET maintenance_mode=0, ... = NULL
           並寫入 audit_logs（action=MAINTENANCE_MODE_AUTO_EXPIRED, actor=SYSTEM）
```

跟 `control_device.py::check_maintenance_mode()` 的 lazy-expiry 邏輯是兩層互補的保險：sweep 保證「就算完全沒人呼叫任何 API，維修模式一樣會準時解除」；`check_maintenance_mode()` 保證「sweep 還沒跑到的最多 `MAINTENANCE_SWEEP_INTERVAL_SECONDS` 秒空窗期裡，不會有已經過期的維修模式誤擋合法的控制指令」。兩者共用同一個 `MAINTENANCE_MODE_AUTO_EXPIRED` 語意，但各自獨立寫入稽核紀錄（不會互相影響或重複觸發同一筆解除，因為第一個跑到的那個會把 `maintenance_mode` 改成 0，另一個之後查到時就不會再符合條件）。

## 8. 安全機制

| 機制 | 使用位置 | 說明 |
|---|---|---|
| bcrypt 密碼雜湊 | `register.py`、`login.py`、`generate_guest_qr.py` | 密碼絕不明文儲存 |
| ECDH (SECP256R1) + HKDF-SHA256 | `device_pair.py` | 裝置配對時派生 session key；資料庫只存 `session_key_hash`，不存明文金鑰 |
| SHA-256 雜湊令牌 | `control_device.py`（guest_token 驗證）、`issue_guest_token_demo.py` | `guest_tokens.token_hash` 存雜湊而非明文；驗證時重新雜湊比對 |
| Ed25519 韌體簽章 | `mqtt-server/sign_firmware.py` + Arduino `Ed25519::verify()`，由 `API/ota_update/ota_update.py` 觸發 | OTA 韌體完整性/來源驗證（簽章機制詳見根目錄 README/HANDOVER）；`API/` 只負責身分驗證後發布觸發訊息，不參與簽章本身 |
| MQTT TLS | `mqtt_tls.py`（`API/` 與 `mqtt-server/` 共用同一套介面） | Broker 只開 8883（TLS），未使用 TLS 即無法連線；見第 10 節 |
| 零信任 `policy_rules` 引擎 | `control_device.py::evaluate_policy()` | 引擎已實作（角色/裝置/動作三維比對），但**沒有任何 endpoint 能寫入規則**，見第 11 節 |
| 維修模式強制暫停控制 | `maintenance_mode.py`（UC5.1）+ `control_device.py::check_maintenance_mode()` | 開啟維修模式期間拒絕一般 lock/unlock 控制指令，限制受信任的維護視窗，降低維修期間被誤操作/外部操作的風險；見第 6.11、7.4 節 |
| Hash-chain 稽核日誌 | 幾乎所有寫入操作 | 見第 9 節 |

## 9. 稽核日誌與雜湊鏈

`audit_logs` 表用簡化版 hash chain 模擬「公有鏈式稽核紀錄」：每筆新紀錄的 `prev_hash` 取自資料庫中目前最新一筆的 `current_hash`；`current_hash` 則是把本筆交易內容（`command_id`/`actor`/`device_id`/`action`/`parameters`/`status`/`timestamp`/`prev_hash`）用 `sort_keys=True` 序列化後做 SHA-256。這讓任何一筆歷史紀錄被竄改，都會導致後續所有紀錄的 hash 對不上——但目前**沒有任何背景任務會主動驗證整條鏈的完整性**，這個特性只在「事後人工稽核比對」時才有意義。

寫入 `audit_logs` 的動作包括：`DEVICE_REGISTERED`（UC2.1）、`UC2.3_DEVICE_DECOMMISSION`/`_DENIED`/`_NOOP`（UC2.3）、`OTA_TRIGGERED`（UC2.2，含發布成功/失敗兩種 status）、`MAINTENANCE_MODE_ENABLED`/`_DISABLED`/`_AUTO_EXPIRED`（UC5.1，最後一種由 `control_device.py` 或 `mqtt_topic_bridge.py` sweep 寫入，actor 分別是實際操作者或 `SYSTEM`/`mqtt_topic_bridge`）、`CONTROL_DEVICE`（UC4.1/4.2，含 ALLOW 與 DENY）、`DEVICE_STATUS_UPDATE`（狀態回報）、`DASHBOARD_VIEWED`（UC4.3，含被拒絕的查詢）、`DEVICE_EVENT`（bridge 寫入的 doorbell/tamper 事件）。

各腳本對 `audit_logs` 欄位存在性的容錯程度不一：`decommission_device.py`/`control_device.py`/`get_family_dashboard.py` 用 `table_exists`/`get_columns` 動態檢查，欄位不存在就跳過該欄位而不整支失敗；`device_pair.py` 則假設欄位固定存在。這是漸進式開發留下的不一致，目前用共同的 `schema.sql` 可以覆蓋所有腳本的假設，尚未造成實際問題。

## 10. 部署與環境變數

`docker-compose.yml` 定義的相關服務與環境變數：

| 服務 | 關鍵環境變數 | 說明 |
|---|---|---|
| `mysql` | `MYSQL_DATABASE=devicemanagement`、`MYSQL_USER=apiuser`、`MYSQL_PASSWORD=devpass123` | **開發用預設密碼，正式環境必須更換**（見第 11 節） |
| `api` | `DB_HOST=mysql`、`DB_USER`/`DB_PASS`/`DB_PASSWORD`（同時設兩種變數名是因為不同腳本讀的變數名不一致）、`DB_NAME`、`CONTROL_MODE=mqtt`、`MQTT_HOST=mqtt-broker`、`MQTT_PORT=8883`、`MQTT_USE_TLS=1`、`MQTT_CA_CERT=/certs/ca.crt`、`PYTHONPATH=/app` | 對外埠 `8091:8000` |
| `api-mqtt-bridge` | 同 `api`（少了 `CONTROL_MODE`，橋接程式本身不需要） | 同一個 image，`command` 覆寫成跑 `control_device/mqtt_topic_bridge.py`，無對外埠（純背景常駐程式） |

容器啟動順序：`api`/`api-mqtt-bridge` 都 `depends_on: mysql(healthy) + mqtt-broker(started)`，`mysql` healthcheck 用 `mysqladmin ping`。

### 常用驗證指令

```bash
docker compose up -d --build
docker exec -it iot_mysql mysql -uapiuser -pdevpass123 devicemanagement -e "SHOW TABLES;"

curl -X POST http://localhost:8091/register \
  -d '{"payload":{"user_id":"admin001","username":"Admin","password":"test1234","email":"a@test.com","phone_number":"0900000000"}}'

# 手動建立家庭（目前沒有對應 endpoint）
docker exec -it iot_mysql mysql -uapiuser -pdevpass123 devicemanagement \
  -e "INSERT INTO families (family_name, admin_uid) VALUES ('測試家庭','admin001');"

curl -X POST http://localhost:8091/device_pair \
  -d '{"payload":{"owner_user_id":"admin001","family_id":1,"device_id":"E8:31:CD:82:80:C8","device_type":"smart_lock"}}'

curl -X POST http://localhost:8091/control_device \
  -d '{"payload":{"family_id":1,"device_id":"E8:31:CD:82:80:C8","action":"UNLOCK","auth_type":"user","user_id":"admin001"}}'

# 觸發 OTA（firmware_file 需已放進 mqtt-server/firmware/ 並用 sign_firmware.py 簽過名）
curl -X POST http://localhost:8091/ota_update \
  -d '{"payload":{"family_id":1,"admin_uid":"admin001","device_id":"E8:31:CD:82:80:C8","firmware_file":"SMART-LOCK-V1_1.1.0.bin","version":"1.1.0"}}'

# 開啟維修模式 60 分鐘，之後裝置的 lock/unlock 都會被拒絕，直到手動關閉或時間到自動恢復
curl -X POST http://localhost:8091/maintenance_mode \
  -d '{"payload":{"family_id":1,"admin_uid":"admin001","device_id":"E8:31:CD:82:80:C8","action":"Enable","duration_minutes":60,"reason":"更換電池"}}'

curl "http://localhost:8091/maintenance_mode?device_id=E8:31:CD:82:80:C8"

curl -X POST http://localhost:8091/dashboard \
  -d '{"payload":{"user_id":"admin001","family_id":1}}'
```

## 11. 已知限制與待辦

這些是本白皮書撰寫當下（對照實際程式碼逐項核對後）已確認的落差，非本次整合範圍要解決的問題，列出是為了讓後續開發者不必重新探索一次：

1. **無 Token 機制**：`login.py` 只驗證密碼並回傳資料，不核發任何存取權杖；後續所有 API 呼叫都是靠 payload 裡的明文 `user_id`/`admin_uid` 判斷身分，等同信任呼叫端誠實回報自己的身分。也沒有登出端點。
2. **無「建立家庭」端點**：`families` 表只能靠手動 SQL 建立，UC1.3（閘道器初始化與屋主綁定）與 UC1.4（跨場域協作）皆未實作。
3. **`decommission_device.py` 明確標示「不含身分驗證版」**：任何呼叫者都能除役任意 `device_id`，沒有 Admin 權限檢查。
4. **`ota_update.py` 不驗證韌體檔案是否存在、也不做版本比對**：`firmware_file` 只檢查 `.bin` 副檔名，不會確認該檔（與對應的 `.bin.sig`）真的放在 `mqtt-server/firmware/` 底下；`version` 純紀錄用，不阻擋重複推送或版本倒退。API 容器目前也沒有掛載 firmware 目錄，若要做存在性檢查需額外設計（例如讓 `mqtt-server` 提供一支清單/校驗 HTTP 端點供 `ota_update.py` 呼叫）。
5. **零信任 `policy_rules` 引擎有讀無寫**：`control_device.py::evaluate_policy()` 已經能查詢並套用規則，但沒有任何 endpoint 能新增/修改/刪除 `policy_rules` 資料列，實務上等同「預設全部放行」。
6. **兩套互不相關的訪客機制並存**：`generate_guest_qr.py`（帳號式，走 `user_families`）與 `issue_guest_token_demo.py`（令牌式，走 `guest_tokens`，僅測試用 demo）彼此獨立，沒有互相關聯或統一介面；`guest_tokens.revoked` 欄位存在但沒有任何 endpoint 會去設定它（UC3.5 撤銷訪客令牌未實作）。
7. **`home/security/gateway_{family_id}/auth_sync` 是死代碼**：`respond_invitation.py`/`generate_guest_qr.py`/`update_member_role.py` 都會發布到這個 topic，但沒有任何服務訂閱它，等同通知從未真正送達任何人（UC4.4 分權通知推播未實作）。
8. **`control_device.py` 在 `mqtt` 模式下只有 lock/unlock 有對應的真實動作**：`ALLOWED_ACTIONS` 支援 9 種動作，但目前的 ESP32 韌體與 bridge 的 `ACTION_MAP` 只認得 `LOCK`/`UNLOCK`，其餘動作會在 bridge 被靜默丟棄（會記 log，但呼叫端拿到的是「已發布」的樂觀回應，不會知道實際上沒有送達裝置）。
9. **`mqtt_status_worker.py` 在目前拓樸下未被使用**：它假設的 `home/{family_id}/device/{device_id}/status` topic 從未被任何發布者使用；真正生效的是 `mqtt_topic_bridge.py`。保留此檔案是因為它是「若未來改用該慣例」的可用參考實作，非本次整合刻意選擇的路徑。
10. **開發用資料庫密碼寫死在 `docker-compose.yml`**（`devroot123`/`devpass123`）：正式環境部署前必須更換，並考慮改用 Docker secrets 或外部 `.env` 檔案而非直接寫在 compose 檔裡。
11. **MQTT 目前只有加密（TLS）沒有身份驗證**：`mosquitto.conf` 仍是 `allow_anonymous true`，任何能連上 broker 網段的裝置都能發布/訂閱任意 topic（只是連線內容加密，無法被竊聽/竄改，但沒有「你是誰」的驗證）。
12. **ECDH 配對在無真實 ESP32 時會模擬裝置端金鑰**（`device_pair.py` 的 `simulated_device=True` 分支）：這讓 UC2.1 的資料庫/稽核鏈路可以獨立於硬體驗證，但也代表「裝置真的擁有對應 private key」這件事在模擬模式下沒有被驗證——真實部署時必須確保 ESP32 端會傳入自己的 `device_public_key_pem`，否則配對記錄只是自欺欺人。
13. **UC5.1 維修模式沒有硬體診斷埠、也沒有條件式自動觸發**：`maintenance_mode.py` 只做「Admin 手動開關＋強制設定最長有效時間＋到期自動恢復」，UC 原始描述裡「對外開放受限的硬體診斷埠」需要 ESP32 韌體實際開一個限定存取的除錯介面，這部分完全沒有實作（也還沒有能驗證的硬體）；「依設定條件觸發」（例如低電量、異常感測自動進入維修模式）也還沒有規則引擎去支援。
14. **維修模式自動恢復有最多 `MAINTENANCE_SWEEP_INTERVAL_SECONDS`（預設 60 秒）的延遲窗口**：`api-mqtt-bridge` 的背景 sweep 是輪詢式而非精準計時器，理論上一個裝置的維修模式可能在到期後最多晚 60 秒才被 sweep 清除；`control_device.py` 有做 lazy-expiry 補洞（見第 7.4 節），但只補了「控制指令」這條路徑——如果之後有其他地方也需要即時反映維修狀態（例如即時通知），一樣需要各自補上同樣的過期檢查，而不是假設 `devices.maintenance_mode` 欄位本身永遠即時準確。
