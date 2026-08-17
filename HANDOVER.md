# IoT-MQTT-Env 工作交接筆記

> 最後更新：2026-07-21（Windows 開發機 → 準備轉移到 Linux + Docker）

## 系統架構

```
ESP32 (SMART-LOCK-V1)          Docker (docker-compose.yml @ repo 根目錄)
  E8:31:CD:82:80:C8   ──WiFi──►  mqtt_service  (eclipse-mosquitto:2, TLS port 8883、9001 明碼)
        (TLS)                        │
                                 mqtt_server   (Python, mqtt-server/, 自動 build)
                                 node_red      (port 1880)
```

MQTT 全走 TLS，明碼 1883 已關閉，見下方「MQTT TLS 加密」。

## MQTT 協定

| Topic | 方向 | Payload |
|---|---|---|
| `home/register` | 裝置 → server | `{"model": "SMART-LOCK-V1", "mac": "..."}` |
| `home/device/<mac>/config` | server → 裝置（retained） | models.yaml 的型號設定（含 `auto_lock_sec`） |
| `home/device/<mac>/cmd` | server → 裝置 | `{"action": "unlock" \| "lock" \| "doorbell_ack"}` |
| `home/device/<mac>/state` | 裝置 → server | `{"locked": true/false}` |
| `home/device/<mac>/event` | 裝置 → server | `{"type": "doorbell" \| "tamper_detected"}` |
| `home/device/<mac>/ota` | server → 裝置 | `{"url": "http://<host>:8080/firmware/xxx.bin", "version": "..."}`，觸發 OTA 更新 |

注意：server 的 handler 是收到 register 時才掛上，**先啟動 server 再讓 ESP32 上電**（或按 reset 重新註冊），否則 state/event 會被忽略。

## 已驗證項目 ✓

- [x] ESP32 註冊 → 收到 config（`auto_lock_sec=5`）→ 回報 state
- [x] 遠端 unlock/lock（`test_cmd.py`），ESP32 即時回應、LED 亮滅、5 秒自動上鎖
- [x] Docker 三容器（mosquitto / mqtt-server / node-red）互通
- [x] **MQTT TLS**：mosquitto 只開 8883、關掉 1883 後，`mqtt-server`、`api`、`api-mqtt-bridge`、`mosquitto_sub` CLI 都用自簽 CA 成功握手 TLS 1.3，`test_register.py`／`control_device` → 橋接 → 真實裝置 topic 全流程重測過一次都正常——**但這些都是 Python/CLI 端，Arduino 端的 `WiFiClientSecure` 還沒在實體 ESP32 上跑過**
- [ ] **觸控腳事件（doorbell/tamper）— 尚未驗證**：原本固定門檻 30 沒反應，已改成開機自動校準 + 每 2 秒印讀值，待重新燒錄測試（見下方「觸控除錯」）

## Linux 遷移步驟

```bash
git clone <repo> && cd IoT-MQTT-Env
docker compose up -d --build
docker logs -f mqtt_server        # 應印出「Broker 連線成功」
```

1. `mqtt-server/Dockerfile` 裡 pip 的 `--trusted-host` 參數是因為原開發機網路有 HTTPS 憑證攔截，**新環境網路正常的話建議拿掉**
2. 防火牆開 TCP 8883（ESP32 要連入，TLS）、1880（Node-RED 編輯器）
3. 查新主機區網 IP（`ip addr`），更新 Arduino sketch 的 `MQTT_BROKER` 後重新燒錄
4. **換新機器/換區網 IP 一定要重新產生 TLS 憑證**：`cd config/certs && ./generate_certs.sh <新IP>`，CA 不變（不用重貼 Arduino sketch 的 `MQTT_ROOT_CA`），只有 server 憑證要重簽——沒做這步 ESP32 連線會失敗（SAN 對不上新 IP）
5. 換行符：repo 已加 `.gitattributes` 強制全部文字檔用 LF（不管 clone 那台機器的 `core.autocrlf` 設定），在 Linux 上 clone/checkout 不會有 CRLF 問題；純粹是保險措施，不代表遷移前需要額外處理

## Arduino 端

- Sketch：`Arduino/MqttSmartLock/MqttSmartLock.ino`（repo 版的 WiFi 帳密是佔位字串，**帳密不要 commit**）
- 程式庫：**PubSubClient 2.8**（knolleary）+ **Crypto**（Rhys Weatherley，提供 `Ed25519.h`，OTA 簽章驗證用），IDE 程式庫管理員搜尋安裝即可；`HTTPClient.h`/`Update.h`/`mbedtls/sha256.h`/`WiFiClientSecure.h` 是 ESP32 core 內建，不用額外安裝
- MQTT 用 `WiFiClientSecure`（TLS，8883），OTA 韌體下載維持明碼 HTTP（兩條分開的傳輸層）
- 板子：ESP32 Dev Module，序列埠 115200
- 測試板腳位（relay 用 LED 代替、按鈕用觸控腳代替）：

| 腳位 | 功能 |
|---|---|
| GPIO2 | 狀態燈（開鎖亮） |
| GPIO15 | relay 替代 LED（開鎖亮） |
| GPIO27 (T7) | 門鈴觸控 |
| GPIO32 (T9) | 防拆觸控 |

（models.yaml 定義的正式腳位是 relay=26 / doorbell=27 / tamper=25，正式硬體時要改回）

### 觸控除錯（目前卡住的點）

觸摸判定 = 讀值偏離開機基準值 1/3 以上（`isTouched()`），適配新舊 ESP32 core 的不同數值範圍。燒錄後看序列埠：

1. 開機印 `[TOUCH] 基準值 doorbell(GPIO27)=xx tamper(GPIO32)=yy`（開機頭幾秒不要碰腳位）
2. 每 2 秒印目前讀值；手指摸**金屬針腳**觀察是否偏離基準
3. 有偏離但不觸發 → 把 `isTouched()` 的 `base / 3` 改 `base / 4`（更靈敏）
4. 讀值完全不動 → 手指沒接觸到金屬，插一條杜邦線到腳位、摸金屬頭
5. 確認 OK 後可刪掉 loop 裡的 `[TOUCH]` debug 輸出區塊

## MQTT TLS 加密（新加，Python/CLI 端已驗證，Arduino 端還沒真機測過）

broker（mosquitto）現在**只開 8883（TLS），1883 明碼已經關掉**，`mosquitto.conf` 加了 `cafile`/`certfile`/`keyfile` 指向 `/mosquitto/certs/`（掛 `config/certs/`）。

```bash
# 產生自簽 CA + server 憑證（第一次跑，或換機器/換 IP 時重跑）
cd config/certs
./generate_certs.sh 192.168.1.3   # 參數是 mosquitto 所在機器的區網 IP
```

- CA（`ca.crt`/`ca.key`）只會產生一次，重跑腳本不會覆蓋；換 IP 只重簽 `server.crt`，Arduino sketch 貼的 `MQTT_ROOT_CA`（CA 的公開憑證）不用重貼、不用重新燒錄
- `ca.key`/`server.key` 是私鑰，已加進 `.gitignore`；`ca.crt`/`server.crt` 可以進版控
- `docker-compose.yml` 的 `mqtt-server`/`api`/`api-mqtt-bridge` 都已經掛 `./config/certs:/certs:ro` + 設好 `MQTT_USE_TLS=1`/`MQTT_CA_CERT=/certs/ca.crt`/`MQTT_PORT=8883`；共用邏輯抽成 `mqtt-server/mqtt_tls.py` 跟 `API/mqtt_tls.py` 兩支模組（api 服務另外設了 `PYTHONPATH=/app`，讓每個 CGI 子行程不管自己在哪個子目錄都 import 得到）
- Arduino sketch：`WiFiClient` 換成 `WiFiClientSecure`，`MQTT_PORT` 改 8883，貼 CA 憑證進 `MQTT_ROOT_CA`，`setup()` 裡先跑 `syncTime()`（NTP 對時）再 `espClient.setCACert(...)`——**TLS 驗證憑證效期需要正確時間，ESP32 沒有 RTC，對不到時間 TLS 連線一定失敗**
- 只加密傳輸，**沒有身分驗證**：`allow_anonymous true` 還在，帳密驗證是另一個獨立的待辦

已驗證：mosquitto 只留 8883 後，`docker exec mqtt_server python test_register.py` 完整跑過（TLS 1.3 握手成功、註冊/config/收訊全部正常）；`api` 的 `control_device` → `api-mqtt-bridge` → 真實裝置 topic 那條路也用 curl + `mosquitto_sub --cafile` 重測過一次，都正常。**Arduino 端的 `WiFiClientSecure`/`syncTime()` 這段完全沒在實體機器上跑過**，下次燒錄記得注意序列埠有沒有印 NTP 對時失敗或 TLS handshake 錯誤。

## OTA 更新（新加，尚未在實體 ESP32 上驗證過；簽章驗證邏輯已寫完但也還沒真機測過）

流程：韌體簽章 → 丟進 `mqtt-server/firmware/` → 對指定裝置發送 `home/device/<mac>/ota`（帶下載網址）→ ESP32 邊下載邊算 SHA-256、寫入 OTA 分區 → 下載完驗證 Ed25519 簽章 → 通過才切換分區、重開機；驗證失敗就中止，繼續跑原本的韌體。完整步驟、疑難排解見 [Arduino/MqttSmartLock/OTA.md](Arduino/MqttSmartLock/OTA.md)。

```bash
# 0.（只需要做一次）產生簽章金鑰對，公鑰要貼進 Arduino sketch 的 OTA_PUBLIC_KEY
docker exec mqtt_server python ota_keys/generate_keypair.py

# 1. Arduino IDE：草稿碼 → 匯出編譯二進位檔，把產生的 .bin 複製到 mqtt-server/firmware/，
#    建議照 mqtt-server/firmware/README.md 的命名慣例改檔名

# 2. 簽章（沒簽過的 .bin，ESP32 會直接拒絕更新）
docker exec mqtt_server python sign_firmware.py firmware/SMART-LOCK-V1_1.1.0.bin

# 3. 觸發 OTA（mac / 檔名 / 版本號都可省略，用預設值）
docker exec mqtt_server python test_ota.py E8:31:CD:82:80:C8 SMART-LOCK-V1_1.1.0.bin 1.1.0

# 4. 序列埠應該會看到 [OTA] 下載簽章 → 下載韌體 → SHA-256 → 簽章驗證通過 → 自動重開機 → 重新連線註冊
```

注意事項：

- `test_ota.py` 裡的 `OTA_HOST`（預設 `192.168.1.3`）要跟 Arduino sketch 的 `MQTT_BROKER` 是同一台機器的區網 IP，因為 ESP32 是直接對這個位址發 HTTP GET 下載 `.bin`，容器內部 IP 對外部裝置沒用
- `docker-compose.yml` 已把 mqtt-server 的 `8080` 對外開放；換機器/換網路記得防火牆也要開這個 port
- 目前沒有版本比對機制，`version` 欄位只是紀錄用，server 端不會檢查裝置目前版本就直接觸發更新
- **簽章私鑰**（`mqtt-server/ota_keys/ota_signing_key.pem`）已加進 `.gitignore`，不會進版控；換一台開發機記得把這個檔案帶過去，不然舊裝置上的公鑰會對不起來，之後簽的韌體全部推不動
- Arduino 端要額外裝 **Crypto**（by Rhys Weatherley）函式庫才有 `Ed25519.h`；`HTTPClient`/`Update`/`mbedtls` 是 ESP32 core 內建的，不用額外裝
- 燒錄失敗（例如網路中斷、韌體檔案損毀）ESP32 會維持原韌體並印 `[OTA] 失敗`，不會變磚，可以重新觸發

## 常用指令

```bash
# 看 server 即時 log（門鈴🔔 / 防拆⚠️ / 狀態變化都在這）
docker logs -f mqtt_server

# 遠端開鎖測試（test_cmd.py 的 MAC 目前已設為 E8:31:CD:82:80:C8）
docker exec mqtt_server python test_cmd.py

# 單發 unlock（測 5 秒自動上鎖）—— broker 只認 TLS，mosquitto_pub 要帶 --cafile + -p 8883
docker exec mqtt_service mosquitto_pub --cafile /mosquitto/certs/ca.crt -p 8883 -t "home/device/E8:31:CD:82:80:C8/cmd" -m '{"action":"unlock"}'

# 模擬裝置註冊 / 事件（不需要實體 ESP32）
docker exec mqtt_server python test_register.py
docker exec mqtt_server python test_event.py

# 觸發 OTA（見上方「OTA 更新」章節）
docker exec mqtt_server python test_ota.py E8:31:CD:82:80:C8 SMART-LOCK-V1.bin 1.1.0

# 改 server code 後重啟（mqtt-server/ 目錄掛載進容器，不用 rebuild）
docker compose restart mqtt-server
```

## 檔案地圖

```
docker-compose.yml        # mqtt-broker / mqtt-server / node-red / mysql / api / api-mqtt-bridge
config/mosquitto.conf     # broker 設定（TLS-only 8883、允許匿名、persistence）
config/certs/             # generate_certs.sh + CA/server 憑證（私鑰不進版控）
mqtt-server/
  main.py                 # 入口，MQTT_BROKER 環境變數指定 broker（預設 localhost）
  mqtt_tls.py              # 共用 MQTT TLS 設定
  registry.py             # 註冊邏輯，寫 devices.json、回傳 config（retained）
  models.yaml             # 型號定義（SMART-LOCK-V1 / SMART-STRONGBOX-V1）
  handlers/lock.py        # lock 型號的 state/event 處理
  handlers/strongbox.py   # strongbox 型號
  test_*.py               # 測試腳本（都吃 MQTT_BROKER 環境變數）
  firmware/               # OTA 韌體檔案（.bin/.sig 不進版控），http://<host>:8080/firmware/ 提供下載
  sign_firmware.py        # 對韌體 SHA-256 做 Ed25519 簽章，輸出 .sig
  ota_keys/               # generate_keypair.py + 私鑰（.pem 不進版控，公鑰 .h 可進版控）
  Dockerfile              # python:3.12-slim（注意 --trusted-host 註記）
Arduino/MqttSmartLock/    # ESP32 測試韌體
API/                      # 家庭/裝置管理 CGI 後端，見「API 整合」章節
```

## API 整合（`api` / `api-mqtt-bridge` / `mysql`）

`API/` 原本完全沒有部署設定、也沒接過 `mqtt-server`。現在：

- `mysql` 容器啟動時自動跑 `API/schema.sql` 建表（`devicemanagement` DB）
- `api` 容器跑 `API/gateway.py`（FastAPI），把 12 支 CGI 腳本包成 `http://<host>:8091/<endpoint>` 的 HTTP API
- `api-mqtt-bridge` 容器跑新增的 `API/control_device/mqtt_topic_bridge.py`，把 `control_device.py`／`mqtt_status_worker.py` 原本假設的 `home/{family_id}/device/{device_id}/...` topic 跟 `mqtt-server` 實測過的 `home/device/<mac>/...` 接起來（細節見 README「API 後端」章節）——**`control_device.py` 和 `mqtt_status_worker.py` 本身完全沒改**，都是新增橋接腳本處理
- 新增 `POST /ota_update`（`API/ota_update/ota_update.py`）：Admin 驗證通過後，直接對 mqtt-broker 發布跟 `mqtt-server/test_ota.py` 相同格式的 OTA 觸發訊息（`home/device/<mac>/ota`），取代原本只能進 `mqtt-server` 容器手動跑腳本的方式；`api` 服務新增 `OTA_HOST` 環境變數（組韌體下載網址用，須為 ESP32 能連到的區網 IP）
- 新增 `GET`\|`POST /maintenance_mode`（`API/maintenance_mode/maintenance_mode.py`，UC5.1）：Admin 開關指定裝置的維修模式，開啟時強制填 `duration_minutes`（上限 `MAX_MAINTENANCE_MINUTES`，預設 240 分鐘）；`control_device.py` 新增 `check_maintenance_mode()`，維修模式開啟期間會拒絕 lock/unlock；`mqtt_topic_bridge.py` 新增背景執行緒（每 `MAINTENANCE_SWEEP_INTERVAL_SECONDS`，預設 60 秒跑一次），時間到自動清除維修狀態並寫稽核紀錄——**這次新增 `devices` 表的 3 個欄位（`maintenance_mode`/`maintenance_expires_at`/`maintenance_reason`），既有已跑過的 dev DB 需要手動 `ALTER TABLE` 或重建，見 `API/WHITEPAPER.md` 第 4 節**

已用真實 ESP32 的 MAC（`E8:31:CD:82:80:C8`）走過一次完整驗證：`register` → `login` → 手動建 `families` → `device_pair` → `control_device`(action=UNLOCK) → 橋接轉成 `unlock` 送到 `home/device/<mac>/cmd` → 模擬 `state`/`event` 回報 → `dashboard` 正確顯示 `physical_state=UNLOCKED`。`ota_update` 與 `maintenance_mode` 兩個新端點本身（權限檢查 → MQTT 發布/資料庫更新 → 稽核寫入這幾段程式邏輯）都還沒實機驗證，見待辦第 2、3 項。

## 待辦

1. 驗證觸控腳 doorbell / tamper 事件（燒錄新版 sketch → 摸腳位 → 看 server log）
2. **OTA（含簽章驗證）尚未在實體 ESP32 上跑過**：簽章的產生/驗證邏輯已經在 server 端用假韌體交叉驗證過（正常簽章通過、竄改內容會正確被拒絕），但 Arduino 端「邊下載邊算 SHA-256 邊寫入 OTA 分區」這段從沒真機測過，需要實際燒錄驗證：正常簽章能更新成功、竄改過的 `.bin` 或漏簽的韌體會被裝置端拒絕且不會變磚。新增的 `POST /ota_update` 端點也還沒實機測過（Admin 權限檢查 → MQTT 發布這段目前只確認過程式邏輯沒有語法/連線錯誤）
3. `ota_update.py` 不檢查 `firmware_file` 是否真的存在於 `mqtt-server/firmware/`、也不比對版本，打錯檔名或忘記簽章要等裝置端下載/驗證失敗才會發現，之後可以考慮讓 `mqtt-server` 開一支清單/校驗端點供 `ota_update.py` 呼叫
4. handlers 的 TODO：doorbell 推播通知（Telegram / ntfy）、tamper 緊急警報
5. Node-RED flow 接上 `home/#` 主題做視覺化（node_red 容器已在 compose 裡）
6. 正式硬體：relay 改回 GPIO26、實體按鈕取代觸控腳，讓韌體改讀 config 回傳的 `pins` 而不是寫死
7. `API/` 沒有「建立家庭」的 endpoint，目前得手動 `INSERT INTO families`，之後要補一支
8. `docker-compose.yml` 裡 MySQL 的帳密（`devroot123`/`devpass123`）是本機開發用預設值，正式環境要換成真的密碼並考慮不要 commit 進 repo
9. `control_device.py` 走 `mqtt` 模式目前只支援 `LOCK`/`UNLOCK`，其餘動作韌體不支援，橋接會直接丟棄
10. `respond_invitation.py` / `generate_guest_qr.py` 發布的 `home/security/gateway_{family_id}/auth_sync` 沒有任何 subscriber，是死代碼，之後要嘛接上要嘛清掉
11. **MQTT TLS 的 Arduino 端沒有在實體 ESP32 上跑過**：`WiFiClientSecure` + `syncTime()`（NTP 對時）這段完全沒真機測過，下次燒錄要注意序列埠有沒有 NTP 對時失敗或 TLS handshake 錯誤
12. MQTT 目前只加密傳輸（TLS），`allow_anonymous true` 還在，沒有帳密驗證，之後要做的話是獨立的一個工作項
13. **UC5.1 維修模式（`POST /maintenance_mode`）還沒實機測過**：Admin 權限檢查 → `devices` 欄位更新 → `control_device.py` 擋控制指令 → `api-mqtt-bridge` 背景 sweep 到期自動恢復，這一整條路徑目前只確認過程式邏輯沒有語法錯誤，沒有實際跑過 Docker Compose 驗證；也還沒有「對外開放受限的硬體診斷埠」（需要 ESP32 韌體配合）跟「依設定條件自動觸發」這兩塊
14. **既有 dev DB 需要手動補 `devices` 新欄位**：`maintenance_mode`/`maintenance_expires_at`/`maintenance_reason` 只會在 `mysql` 容器第一次啟動時由 `schema.sql` 建立，已經跑過的環境要手動 `ALTER TABLE` 或砍掉 `./mysql-data` 重建（指令見 `API/WHITEPAPER.md` 第 4 節）
