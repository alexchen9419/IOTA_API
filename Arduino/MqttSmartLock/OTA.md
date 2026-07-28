# MqttSmartLock OTA 更新指南

這份文件只講「怎麼幫這顆 ESP32 做 OTA、怎麼產生跟使用 `.bin`」。系統整體架構看 [../../HANDOVER.md](../../HANDOVER.md)、[../../README.md](../../README.md)。

## 這套 OTA 在做什麼

1. Server（`mqtt-server`）把編譯好的韌體 `.bin` 用 HTTP 提供下載（`http://<host>:8080/firmware/<檔名>`）
2. Server 對指定裝置的 MAC 發布 `home/device/<mac>/ota`，內容是下載網址
3. ESP32 收到後：
   - 先下載對應的 `.sig`（韌體 SHA-256 雜湊值的 Ed25519 簽章，64 bytes）
   - 邊下載 `.bin` 邊寫進 OTA 分區、邊累加算 SHA-256（不會把整包韌體塞進記憶體）
   - 下載完，用寫死在 sketch 裡的 `OTA_PUBLIC_KEY` 驗證簽章
   - **驗證通過** → 切換開機分區、`ESP.restart()` 重開機，跑新韌體
   - **驗證失敗**（簽章不符 / 沒簽章 / 內容被竄改）→ 直接中止，不切換分區，重開機後還是跑原本的韌體，不會變磚

也就是說：**沒有簽過章的 `.bin` 一定推不動**，這是刻意設計的，不是 bug。

## 一次性設置（新開發機才需要）

### 1. Arduino IDE 安裝函式庫

程式庫管理員搜尋安裝：

- **PubSubClient**（by Nick O'Leary）
- **Crypto**（by Rhys Weatherley）— 提供 `Ed25519.h`，OTA 簽章驗證用

`HTTPClient.h`、`Update.h`、`mbedtls/sha256.h` 是 ESP32 core 內建的，不用另外裝。

### 2. 產生簽章金鑰對

```bash
docker exec mqtt_server python ota_keys/generate_keypair.py
```

私鑰會寫在 `mqtt-server/ota_keys/ota_signing_key.pem`（已加進 `.gitignore`，不會進版控——**這個檔案不要弄丟，也不要外流**，弄丟了以後簽的韌體全部推不動，要重新產生一組金鑰、重新燒錄 sketch 換公鑰）。

指令會同時印出（也存在 `mqtt-server/ota_keys/ota_public_key.h`）像這樣的內容：

```c
const uint8_t OTA_PUBLIC_KEY[32] = { 0x3e, 0xce, 0x9b, 0xb3, ... };
```

把這行貼到 `MqttSmartLock.ino` 開頭的 `OTA_PUBLIC_KEY` 那裡（**這是公鑰，貼進 sketch、進版控都沒關係**）。同一組金鑰只要貼一次；除非要換金鑰，否則之後每次簽新韌體都不用重貼。

> 這個金鑰對已經在這個 repo 產生過一次了，`MqttSmartLock.ino` 裡目前貼的就是對應的公鑰。除非私鑰真的遺失，不用重做這一步。

## 每次要推新韌體的流程

### 1. 改版本號

改 `MqttSmartLock.ino` 裡的 `FW_VERSION`（例如 `"1.0.0"` → `"1.1.0"`），這樣燒錄/OTA 完之後可以從序列埠開機 log 確認到底有沒有真的換版本。

### 2. 從 Arduino IDE 匯出 `.bin`

**不要按「上傳」**，那樣會直接燒錄然後把 `.bin` 丟掉。要用：

- Arduino IDE 2.x：**草稿碼（Sketch）→ 匯出已編譯的二進位檔（Export Compiled Binary）**，快捷鍵 `Ctrl+Alt+S`
- Arduino IDE 1.8.x：選單文字可能是英文 "Export compiled Binary"

匯出完，檔案會生在 sketch 資料夾裡（IDE 輸出視窗會印出確切路徑），長得像：

```
Arduino/MqttSmartLock/build/esp32.esp32.esp32/MqttSmartLock.ino.bin
```

### 3. 複製到 `mqtt-server/firmware/` 並改名

命名建議 `<model>_<version>.bin`：

```powershell
Copy-Item "Arduino\MqttSmartLock\build\esp32.esp32.esp32\MqttSmartLock.ino.bin" "mqtt-server\firmware\SMART-LOCK-V1_1.1.0.bin"
```

### 4. 簽章

```bash
docker exec mqtt_server python sign_firmware.py firmware/SMART-LOCK-V1_1.1.0.bin
```

會在旁邊多一個 `firmware/SMART-LOCK-V1_1.1.0.bin.sig`（64 bytes），跟 `.bin` 一起透過 `/firmware/` 提供下載，不用手動再處理它。

### 5. 觸發 OTA

```bash
docker exec mqtt_server python test_ota.py <裝置MAC> SMART-LOCK-V1_1.1.0.bin 1.1.0
# 例：
docker exec mqtt_server python test_ota.py E8:31:CD:82:80:C8 SMART-LOCK-V1_1.1.0.bin 1.1.0
```

### 6. 看序列埠確認結果

正常應該依序看到：

```
[OTA] 下載簽章: http://.../SMART-LOCK-V1_1.1.0.bin.sig
[OTA] 開始下載韌體: http://.../SMART-LOCK-V1_1.1.0.bin
[OTA] 韌體 SHA-256: <64 個 hex 字元>
[OTA] 簽章驗證通過，寫入完成，準備重開機
```

接著裝置會重開機，開機 log 應該印出新的 `[BOOT] 韌體版本 1.1.0`，並重新連上 MQTT、重新註冊。

## 疑難排解

| 序列埠訊息 | 原因 | 怎麼處理 |
|---|---|---|
| `簽章檔下載失敗，HTTP 404` | 忘記跑 `sign_firmware.py`，`.sig` 不存在 | 補簽 |
| `簽章檔大小不對` | `.sig` 檔損毀或不是 64 bytes | 重新簽一次 |
| `下載韌體失敗，HTTP xxx` | `test_ota.py` 裡的 `OTA_HOST` 跟 Arduino sketch 的 `MQTT_BROKER` 不是同一台機器的 IP，或防火牆沒開 `8080` | 檢查 `OTA_HOST`／防火牆 |
| `Update.begin 失敗` | 韌體太大，OTA 分區放不下 | 檢查 Arduino IDE 的 Partition Scheme 設定 |
| `下載不完整 (x/y bytes)` | 網路中斷 | 重新觸發一次即可，裝置不會變磚 |
| `簽章驗證失敗！韌體可能被竄改或不是官方簽發` | `.bin` 內容跟簽章對不上（換過檔案沒重簽、金鑰對不上、或真的被竄改） | 確認 `.bin` 有沒有重新簽章、`OTA_PUBLIC_KEY` 是否跟簽章用的私鑰是同一組 |

以上任何一種失敗，裝置都會維持跑原本的韌體，不需要重新燒錄救援。

## 目前已知限制

- 沒有版本比對機制：server 不會檢查裝置目前的版本，`version` 欄位只是紀錄用，重複觸發同一版本一樣會重新下載燒錄一次
- OTA 觸發目前是手動跑 `test_ota.py`，還沒有「只有 Admin 才能觸發」的身分驗證（也還沒接進 `API/`）
- 這整套簽章驗證邏輯還沒在實體 ESP32 上跑過完整流程，只在 server 端用假韌體交叉驗證過簽章的產生跟驗證是對得上的
