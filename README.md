# IoT-MQTT-Env

以 MQTT 為核心的智慧鎖 IoT 環境，包含兩個子系統，**兩者已串接、共用同一個 MQTT broker 跟同一份即時裝置狀態**：

1. **`mqtt-server/`** — ESP32 裝置透過 MQTT 向後端註冊、回報狀態/事件，後端可下發指令與 OTA 更新，並提供網頁監控介面
2. **`API/`** — 家庭/裝置管理後端：帳號、家庭成員邀請與權限、裝置配對/除役、遠端控制（含訪客權杖與零信任政策）、儀表板查詢，搭配 MySQL 與雜湊鏈稽核日誌

## 系統架構

```text
ESP32 (SMART-LOCK-V1 / SMART-STRONGBOX-V1)     Docker Compose
        │  WiFi + MQTT                          ├─ mqtt_service      (eclipse-mosquitto, 1883/9001)
        └──────────────────┬────────────────────┼─ mqtt_server       (Python, mqtt-server/)
                            │                    │    ├─ 註冊/指令/OTA 韌體伺服器 (8080)
                            │                    │    └─ 網頁監控 web_monitor.py (8090)
                            │                    ├─ node_red          (1880)
                            │                    ├─ mysql             (devicemanagement, 3306)
                            │                    ├─ api               (家庭/裝置管理 CGI 閘道, 8091)
                            └────────────────────┼─ api-mqtt-bridge   (topic 轉譯，見「API 後端」)
```

## 快速開始

```bash
git clone <repo> && cd IOTA_API
docker compose up -d --build
docker logs -f mqtt_server        # 應印出「Broker 連線成功」
```

- 防火牆需開放：TCP `1883`（ESP32 連 broker）、`8080`（OTA 韌體下載）、`8090`（網頁監控）、`8091`（家庭/裝置管理 API）、`3306`（MySQL，除錯用可拿掉）、`1880`（Node-RED）
- Arduino sketch 裡的 `MQTT_BROKER` 要改成跑 Docker 這台機器的區網 IP
- `docker-compose.yml` 裡的 MySQL 密碼是本機開發用的預設值（`devroot123` / `devpass123`），正式環境要換掉
- repo 用 [`.gitattributes`](.gitattributes) 統一文字檔換行符為 LF，Windows 上 clone/編輯不用擔心 CRLF 被 git 標記異動

## MQTT 協定

| Topic | 方向 | Payload |
|---|---|---|
| `home/register` | 裝置 → server | `{"model": "SMART-LOCK-V1", "mac": "..."}` |
| `home/device/<mac>/config` | server → 裝置（retained） | `models.yaml` 的型號設定（含 `auto_lock_sec`） |
| `home/device/<mac>/cmd` | server → 裝置 | `{"action": "unlock" \| "lock" \| "doorbell_ack"}` |
| `home/device/<mac>/state` | 裝置 → server | `{"locked": true/false}` |
| `home/device/<mac>/event` | 裝置 → server | `{"type": "doorbell" \| "tamper_detected"}` |
| `home/device/<mac>/ota` | server → 裝置 | `{"url": "http://<host>:8080/firmware/xxx.bin", "version": "..."}` |

裝置已在 `devices.json` 註冊過的話，重連只會更新 `last_seen`、不會重發 config；server 端 handler 仍會在每次收到 `home/register` 時重新掛上（避免 server 重啟後漏接 state/event）。

## 網頁監控

`mqtt-server/web_monitor.py` 提供即時裝置狀態 / 事件記錄的網頁介面，開發機瀏覽器打開 `http://<host>:8090` 即可看到裝置列表、上鎖/解鎖按鈕、門鈴與防拆事件紀錄。

目前尚未納入容器的常駐啟動流程，需要時手動啟動：

```bash
docker exec -d mqtt_server python -u web_monitor.py
```

## OTA 更新

Arduino IDE 只能透過 USB 燒錄，要 OTA 需先「匯出編譯後的二進位檔」拿到 `.bin`：

```bash
# 1. Arduino IDE：草稿碼 → 匯出已編譯的二進位檔，把 .bin 複製到 mqtt-server/firmware/
# 2. 觸發 OTA
docker exec mqtt_server python test_ota.py <mac> <檔名.bin> <版本號>
```

裝置會透過 `HTTPUpdate` 下載 `.bin` 並自動燒錄、重開機。詳細注意事項見 [HANDOVER.md](HANDOVER.md#ota-更新新加尚未在實體-esp32-上驗證過)。

## Arduino 端

- Sketch：`Arduino/MqttSmartLock/MqttSmartLock.ino`（repo 版的 WiFi 帳密是佔位字串，**帳密不要 commit**）
- 板子：ESP32 Dev Module，序列埠 115200
- 需要安裝：**PubSubClient 2.8**（knolleary）；`HTTPUpdate.h` 是 ESP32 core 內建，不用額外裝

## 常用測試指令

```bash
docker exec mqtt_server python test_register.py   # 模擬裝置註冊
docker exec mqtt_server python test_cmd.py        # 模擬 unlock/lock 指令
docker exec mqtt_server python test_event.py      # 模擬 doorbell/tamper 事件
docker exec mqtt_server python test_ota.py        # 觸發 OTA
```

## API 後端（`API/`）

家庭/裝置管理用的 CGI API：每支 `.py` 都是獨立的 CGI 腳本（從 stdin 讀 JSON payload、印 `Status:` header + JSON 回應），搭配 MySQL（`devicemanagement` 資料庫）。每個資料夾旁都附規範 PDF，是實際的介面契約文件。現在透過 `api`（FastAPI 閘道，見 [`API/gateway.py`](API/gateway.py)）跑在 Docker Compose 裡，`http://<host>:8091/<endpoint>` 呼叫，閘道會把 HTTP request 轉成 CGI 呼叫方式跑對應腳本，並把腳本印出的 `Status:` 正確轉成 HTTP 狀態碼。

| Endpoint | 對應腳本 / 用例 | 功能 |
|---|---|---|
| `POST /login` | `login/login.py` | 帳號登入、回傳使用者所屬家庭清單 |
| `POST /register` | `register/register.py` | 帳號註冊（bcrypt 雜湊密碼） |
| `POST /send_invitation` | `send_invitation/` | Admin 邀請使用者加入家庭 |
| `POST /respond_invitation` | `respond_invitation/` | 受邀者接受/拒絕邀請 |
| `POST /update_member_role` | `update_member_role/` | Admin 變更家庭成員角色（Admin/Member/Guest/Technician/SP/Revoked），內建自我保護（不能自我撤權） |
| `POST /generate_guest_qr` | `generate_guest_qr/` | Admin 產生訪客帳號 QR code（重用閒置訪客帳號或新建） |
| `POST /device_pair` | `device_pair/`（UC2.1） | 裝置首次安全配對：ECDH 金鑰交換 + HKDF 產生 session key，只存 hash |
| `GET`\|`POST /list_devices` | `list_devices/`（UC2.1） | 查詢已配對裝置清單與最近上鏈（audit_logs）紀錄 |
| `POST /decommission_device` | `decommission_device/`（UC2.3） | 裝置除役/解綁：`status=Revoked`、清空 session_key_hash |
| `POST /control_device` | `control_device/control_device.py`（UC4.1/4.2） | 遠端控制（lock/unlock 等）：驗證家庭角色或訪客權杖 + 零信任 policy_rules，`CONTROL_MODE=mqtt` 時真的發 MQTT 指令 |
| `POST /device_status_update` | `control_device/device_status_update.py`（UC4.1/4.2） | 裝置執行結果回呼（更新 `control_commands` / `device_telemetry`） |
| `POST /dashboard` | `dashboard/get_family_dashboard.py`（UC4.3） | 家庭儀表板：裝置清單 + 最新遙測 + 連線健康度，寫入 `DASHBOARD_VIEWED` 稽核 |

`control_device/issue_guest_token_demo.py` 跟 `control_device/mqtt_status_worker.py` 沒有掛進閘道（前者是手動測試工具、後者已被 `mqtt_topic_bridge.py` 取代，見下），要用就直接 `docker exec iot_api python control_device/issue_guest_token_demo.py` 跑。

**稽核日誌**：`audit_logs` 用 `prev_hash` + `current_hash` 串成雜湊鏈（類似區塊鏈不可篡改的概念），`control_device.py` / `decommission_device.py` / `get_family_dashboard.py` 等都會寫入。

### 跟 mqtt-server 的整合（`api-mqtt-bridge`）

`control_device.py`（`CONTROL_MODE=mqtt`）跟 `mqtt_status_worker.py` 原本假設的 topic 是 `home/{family_id}/device/{device_id}/cmd`\|`status`，但 `mqtt-server` + 真實 ESP32 韌體用的是 `home/device/<mac>/cmd`\|`state`\|`event`（唯一實測過的慣例）。**沒有改 `control_device.py` 或 `mqtt_status_worker.py` 本身**，而是新增一支獨立橋接腳本 [`API/control_device/mqtt_topic_bridge.py`](API/control_device/mqtt_topic_bridge.py)（`api-mqtt-bridge` 服務）：

- 訂閱 `home/+/device/+/cmd`（`control_device.py` 發的）→ action 轉小寫（`UNLOCK`→`unlock`，韌體不支援的動作直接丟棄不轉發）→ 發到 `home/device/<mac>/cmd`（真實裝置訂閱的 topic）
- 訂閱 `home/device/+/state`（真實 ESP32 回報 `{"locked":bool}`）→ 用 `device_id`(=mac) 查 `devices` 表拿 `family_id` → 轉成 `device_status_update.handle_status()` 需要的格式直接呼叫（沒有走 MQTT 轉發，直接 function call）
- 訂閱 `home/device/+/event`（doorbell/tamper）→ 直接寫一筆 `audit_logs`（`action=DEVICE_EVENT`），因為 API 這邊原本沒有對應這種事件的 UC

裝置要先透過 `/device_pair` 配對過（`devices` 表要有這個 `device_id` 對應的 `family_id`），橋接才會處理它的 state/event，否則會記 log 跳過。

**已知缺口**：

- 沒有任何 endpoint 可以「建立家庭」（`families` 表）——要測試得手動 `INSERT INTO families`
- `respond_invitation.py` / `generate_guest_qr.py` 另外會發布到 `home/security/gateway_{family_id}/auth_sync`，這條路徑沒有任何 subscriber、跟裝置控制無關，維持原樣未動
- `control_device.py` 走 `mqtt` 模式時目前只有 `LOCK`/`UNLOCK` 會真的送到裝置，其餘動作（ON/OFF/OPEN/CLOSE/TOGGLE/START/STOP）韌體不支援，橋接會丟棄並記 log

**環境變數**：`api` / `api-mqtt-bridge` 兩個服務在 `docker-compose.yml` 裡已經設好 `DB_HOST`/`DB_USER`/`DB_PASS`/`DB_PASSWORD`/`DB_NAME`/`MQTT_HOST`/`MQTT_PORT`/`CONTROL_MODE`（兩種 DB 密碼變數名都設，因為不同腳本讀的變數名不完全一致）。

## 檔案地圖

```text
docker-compose.yml        # mqtt-broker / mqtt-server / node-red / mysql / api / api-mqtt-bridge
config/mosquitto.conf     # broker 設定
mqtt-server/
  main.py                 # 入口：MQTT 註冊/指令處理 + OTA 韌體檔案伺服器 (8080)
  web_monitor.py           # 網頁監控（FastAPI + WebSocket, 8090）
  registry.py              # 註冊邏輯，寫 devices.json、回傳 config（retained）
  models.yaml              # 型號定義（SMART-LOCK-V1 / SMART-STRONGBOX-V1）
  handlers/                # 各型號的 state/event 處理
  firmware/                # OTA 韌體檔案（.bin 不進版控）
  test_*.py                # 測試腳本
Arduino/MqttSmartLock/     # ESP32 韌體
API/                       # 家庭/裝置管理 CGI 後端（見上方「API 後端」章節）
  gateway.py               # FastAPI CGI 閘道，8091 對外（新增）
  schema.sql               # MySQL schema，mysql 容器啟動時自動載入（新增）
  Dockerfile / requirements.txt                 # api / api-mqtt-bridge 共用（新增）
  login/ register/                              # 帳號登入 / 註冊
  send_invitation/ respond_invitation/          # 家庭邀請
  update_member_role/ generate_guest_qr/        # 成員角色管理 / 訪客 QR
  device_pair/ list_devices/ decommission_device/  # 裝置配對 / 清單 / 除役
  control_device/                               # 遠端控制
    control_device.py / device_status_update.py / issue_guest_token_demo.py  # 原始腳本，未修改
    mqtt_status_worker.py    # 原始 worker，假設 home/{family_id}/... topic，目前未使用（被下面取代）
    mqtt_topic_bridge.py     # 新增：橋接 mqtt-server 的真實 topic 慣例
  dashboard/                                     # 家庭儀表板查詢
  各目錄下的 *.pdf                                # 對應功能的規範文件
```

更詳細的開發交接筆記（除錯紀錄、待辦、遷移細節）見 [HANDOVER.md](HANDOVER.md)。
