# IoT-MQTT-Env 工作交接筆記

> 最後更新：2026-07-21（Windows 開發機 → 準備轉移到 Linux + Docker）

## 系統架構

```
ESP32 (SMART-LOCK-V1)          Docker (docker-compose.yml @ repo 根目錄)
  E8:31:CD:82:80:C8   ──WiFi──►  mqtt_service  (eclipse-mosquitto:2, port 1883/9001)
                                      │
                                 mqtt_server   (Python, mqtt-server/, 自動 build)
                                 node_red      (port 1880)
```

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
- [ ] **觸控腳事件（doorbell/tamper）— 尚未驗證**：原本固定門檻 30 沒反應，已改成開機自動校準 + 每 2 秒印讀值，待重新燒錄測試（見下方「觸控除錯」）

## Linux 遷移步驟

```bash
git clone <repo> && cd IoT-MQTT-Env
docker compose up -d --build
docker logs -f mqtt_server        # 應印出「Broker 連線成功」
```

1. `mqtt-server/Dockerfile` 裡 pip 的 `--trusted-host` 參數是因為原開發機網路有 HTTPS 憑證攔截，**新環境網路正常的話建議拿掉**
2. 防火牆開 TCP 1883（ESP32 要連入）、1880（Node-RED 編輯器）
3. 查新主機區網 IP（`ip addr`），更新 Arduino sketch 的 `MQTT_BROKER` 後重新燒錄
4. 換行符：repo 已加 `.gitattributes` 強制全部文字檔用 LF（不管 clone 那台機器的 `core.autocrlf` 設定），在 Linux 上 clone/checkout 不會有 CRLF 問題；純粹是保險措施，不代表遷移前需要額外處理

## Arduino 端

- Sketch：`Arduino/MqttSmartLock/MqttSmartLock.ino`（repo 版的 WiFi 帳密是佔位字串，**帳密不要 commit**）
- 程式庫：**PubSubClient 2.8**（knolleary），IDE 程式庫管理員搜尋安裝即可；`HTTPUpdate.h`（OTA 用）是 ESP32 core 內建，不用額外安裝
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

## OTA 更新（新加，尚未在實體 ESP32 上驗證過）

流程：把編譯好的 `.bin` 丟進 `mqtt-server/firmware/` → 對指定裝置發送 `home/device/<mac>/ota`（帶下載網址）→ ESP32 用 `HTTPUpdate` 下載並自動燒錄、重開機。

```bash
# 1. Arduino IDE：草稿碼 → 匯出編譯二進位檔，把產生的 .bin 複製到 mqtt-server/firmware/，
#    建議照 mqtt-server/firmware/README.md 的命名慣例改檔名

# 2. 觸發 OTA（mac / 檔名 / 版本號都可省略，用預設值）
docker exec mqtt_server python test_ota.py E8:31:CD:82:80:C8 SMART-LOCK-V1.bin 1.1.0

# 3. 序列埠應該會看到 [OTA] 開始下載更新 → 燒錄 → 自動重開機 → 重新連線註冊
```

注意事項：

- `test_ota.py` 裡的 `OTA_HOST`（預設 `192.168.1.3`）要跟 Arduino sketch 的 `MQTT_BROKER` 是同一台機器的區網 IP，因為 ESP32 是直接對這個位址發 HTTP GET 下載 `.bin`，容器內部 IP 對外部裝置沒用
- `docker-compose.yml` 已把 mqtt-server 的 `8080` 對外開放；換機器/換網路記得防火牆也要開這個 port
- 目前沒有版本比對機制，`version` 欄位只是紀錄用，server 端不會檢查裝置目前版本就直接觸發更新
- 燒錄失敗（例如網路中斷、韌體檔案損毀）ESP32 會維持原韌體並印 `[OTA] 失敗`，不會變磚，可以重新觸發

## 常用指令

```bash
# 看 server 即時 log（門鈴🔔 / 防拆⚠️ / 狀態變化都在這）
docker logs -f mqtt_server

# 遠端開鎖測試（test_cmd.py 的 MAC 目前已設為 E8:31:CD:82:80:C8）
docker exec mqtt_server python test_cmd.py

# 單發 unlock（測 5 秒自動上鎖）
docker exec mqtt_service mosquitto_pub -t "home/device/E8:31:CD:82:80:C8/cmd" -m '{"action":"unlock"}'

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
docker-compose.yml        # 三個服務：mqtt-broker / mqtt-server / node-red
config/mosquitto.conf     # broker 設定（0.0.0.0:1883、允許匿名、persistence）
mqtt-server/
  main.py                 # 入口，MQTT_BROKER 環境變數指定 broker（預設 localhost）
  registry.py             # 註冊邏輯，寫 devices.json、回傳 config（retained）
  models.yaml             # 型號定義（SMART-LOCK-V1 / SMART-STRONGBOX-V1）
  handlers/lock.py        # lock 型號的 state/event 處理
  handlers/strongbox.py   # strongbox 型號
  test_*.py               # 測試腳本（都吃 MQTT_BROKER 環境變數）
  firmware/               # OTA 韌體檔案（.bin 不進版控），http://<host>:8080/firmware/ 提供下載
  Dockerfile              # python:3.12-slim（注意 --trusted-host 註記）
Arduino/MqttSmartLock/    # ESP32 測試韌體
API/                      # 家庭/裝置管理 CGI 後端，見「API 整合」章節
```

## API 整合（`api` / `api-mqtt-bridge` / `mysql`）

`API/` 原本完全沒有部署設定、也沒接過 `mqtt-server`。現在：

- `mysql` 容器啟動時自動跑 `API/schema.sql` 建表（`devicemanagement` DB）
- `api` 容器跑 `API/gateway.py`（FastAPI），把 12 支 CGI 腳本包成 `http://<host>:8091/<endpoint>` 的 HTTP API
- `api-mqtt-bridge` 容器跑新增的 `API/control_device/mqtt_topic_bridge.py`，把 `control_device.py`／`mqtt_status_worker.py` 原本假設的 `home/{family_id}/device/{device_id}/...` topic 跟 `mqtt-server` 實測過的 `home/device/<mac>/...` 接起來（細節見 README「API 後端」章節）——**`control_device.py` 和 `mqtt_status_worker.py` 本身完全沒改**，都是新增橋接腳本處理

已用真實 ESP32 的 MAC（`E8:31:CD:82:80:C8`）走過一次完整驗證：`register` → `login` → 手動建 `families` → `device_pair` → `control_device`(action=UNLOCK) → 橋接轉成 `unlock` 送到 `home/device/<mac>/cmd` → 模擬 `state`/`event` 回報 → `dashboard` 正確顯示 `physical_state=UNLOCKED`。

## 待辦

1. 驗證觸控腳 doorbell / tamper 事件（燒錄新版 sketch → 摸腳位 → 看 server log）
2. **OTA 尚未在實體 ESP32 上跑過**：目前只驗證了 MQTT 觸發訊息格式跟 HTTP 檔案下載（`curl` 測得到 200），還沒真的燒錄過一次完整流程
3. handlers 的 TODO：doorbell 推播通知（Telegram / ntfy）、tamper 緊急警報
4. Node-RED flow 接上 `home/#` 主題做視覺化（node_red 容器已在 compose 裡）
5. 正式硬體：relay 改回 GPIO26、實體按鈕取代觸控腳，讓韌體改讀 config 回傳的 `pins` 而不是寫死
6. `API/` 沒有「建立家庭」的 endpoint，目前得手動 `INSERT INTO families`，之後要補一支
7. `docker-compose.yml` 裡 MySQL 的帳密（`devroot123`/`devpass123`）是本機開發用預設值，正式環境要換成真的密碼並考慮不要 commit 進 repo
8. `control_device.py` 走 `mqtt` 模式目前只支援 `LOCK`/`UNLOCK`，其餘動作韌體不支援，橋接會直接丟棄
9. `respond_invitation.py` / `generate_guest_qr.py` 發布的 `home/security/gateway_{family_id}/auth_sync` 沒有任何 subscriber，是死代碼，之後要嘛接上要嘛清掉
