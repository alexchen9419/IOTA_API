# 實機連線檢查清單

對象：要把實體 ESP32 接上這套系統的人。
環境前提：後端跑在 `192.168.50.234`（換機器請整份替換成 `ipconfig` 查到的 IPv4）。

---

## 一、開始之前：三個會擋住你的東西

### 1. TLS 私鑰不在版控裡 ⚠️ 最大的坑

`config/certs/` 只有 `ca.crt` 與 `server.crt`，**沒有 `server.key`**（依 `.gitignore`
私鑰不進版控）。正式的 `config/mosquitto.conf` 是 TLS-only（8883），少了私鑰
mosquitto 起不來，**8883 不會有人在聽**，而韌體預設就是連 8883。

三條路，挑一條：

| 做法 | 要做什麼 | 代價 |
| :-- | :-- | :-- |
| **A. 今天先走明文 1883**（建議） | 韌體 `#define MQTT_USE_TLS 0`，broker 用 dev 設定啟動 | 傳輸沒加密，只適合內網測試 |
| **B. 跟持有私鑰的人要 `server.key`** | 放回 `config/certs/`，維持 TLS 8883 | 要等人 |
| **C. 重新產生憑證** | 跑 `config/certs/generate_certs.sh` | 會產生**新的 CA**，所有已燒錄的 ESP32 內嵌 CA 全部失效，**每一台都要重燒** |

反正 broker IP 跟 WiFi 帳密也要改、今天一定得重燒，所以 A 最省事。
想在正式 demo 走 TLS 的話，另外安排 B。

### 2. 韌體裡的 broker IP 是舊的

`Arduino/MqttSmartLock/MqttSmartLock.ino`：

```cpp
const char* MQTT_BROKER = "192.168.1.3";   // ← 改成 192.168.50.234
```

不能填 `127.0.0.1` 或 `localhost` —— 那是 ESP32 自己。

### 3. WiFi 帳密還是佔位字串

```cpp
const char* WIFI_SSID = "SSID";   // ← 改成真的
const char* WIFI_PASS = "PWD";
```

**ESP32 與後端主機必須在同一個網段**（`192.168.50.x`，gateway `192.168.50.1`）。
用手機開熱點給 ESP32、電腦卻接在別的網路，一定連不上。

---

## 二、已經確認可用的部分

不用再懷疑這幾項，我實測過：

- **Windows 防火牆不擋** —— `192.168.50.234` 在乙太網路介面，屬 Private profile，
  該 profile 的防火牆是關閉的
- **三個服務走區網 IP 都通** —— MQTT 1883、API 8091、觀測台 5501
- **軟體側整條鏈路通** —— App → API → bridge → 裝置 → 狀態回報 → 資料庫，
  連續 6 次控制指令全部 `SUCCEEDED`（用 `mqtt-server/fake_device.py` 驗證）

**但這不代表硬體會通。** 模擬器沒有碰 TLS 握手、NTP 對時、servo 扭力、
WiFi 掉線重連、OTA 寫分區。它的用途是排除法：

- 模擬器過、真機不過 → 問題在韌體或實體層
- 模擬器就不過 → 別接硬體，先修軟體

---

## 三、操作順序

### 步驟 0：把後端叫起來

```bash
cd "IOTA APP"
# 明文 1883（搭配韌體 MQTT_USE_TLS 0）
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
# 若走 TLS 8883（config/certs/server.key 已就位）
# docker compose up -d

curl http://localhost:8091/healthz     # 要回 {"status":"ok"}
```

觀測台（另開一個終端機）：

```bash
cd tools/console && python -m http.server 5501
```

開 <http://192.168.50.234:5501> → 按「連線」→ 登入。

### 步驟 1：燒錄韌體

改好上面三個值，燒進去。序列埠（115200）應該看到：

```
[WiFi] IP: 192.168.50.xxx  MAC: E8:31:CD:82:80:C8
[MQTT] 明文模式（1883）—— 跳過 NTP 對時與憑證驗證      ← MQTT_USE_TLS 0 時
[MQTT] 連線 192.168.50.234 ... 成功
[REGISTER] {"model":"SMART-LOCK-V1","mac":"E8:31:CD:82:80:C8"}
[STATE] {"locked":true}
```

**把那個 MAC 抄下來。**

### 步驟 2：配對（這一步不做，App 永遠看不到裝置）

裝置註冊是**兩套獨立系統，沒有任何自動同步**：

```
ESP32 開機 → home/register → mqtt-server/devices.json    ← :8090 看到的
                    ⛔ 沒有同步 ⛔
App 配對   → /device_pair   → MySQL devices 表           ← App 讀的
```

只做前者的話，bridge 會直接丟掉它的狀態回報：

```
[bridge] E8:31:CD:82:80:C8: not paired (no row in devices table), skipping state update
```

在觀測台：裝置會以 **「⚠️ 未配對裝置」** 出現 → 點「填入配對欄位」→
選好場域 → 按「啟動 ECDH 配對」。

MAC 要**逐字元一致**（大寫、含冒號）。

### 步驟 3：控制

觀測台按「🔓 解鎖」，右欄應該依序出現六條訊息：

```
🔵 POST /control_device                                   手機 → API
🟣 home/4/device/<MAC>/cmd   {"command_id":...,"action":"UNLOCK"}
🟪 home/device/<MAC>/cmd     {"action":"unlock"}          bridge 翻譯
🟢 home/device/<MAC>/state   {"locked":false}             裝置回報
🔵 ← 200 Success
```

中欄六個 hop 會依序亮起。**哪一個不亮，問題就在那一段。**

實體行為：servo 轉動、狀態燈亮。**解鎖 5 秒後韌體會自動上鎖**
（`loop()` 的 auto-lock），畫面上狀態會自己變回「已上鎖」——這是設計，不是 bug。

---

## 四、卡住時對照這張表

| 症狀 | 檢查 |
| :-- | :-- |
| 序列埠一直印 `[MQTT] 失敗 rc=-2` | broker IP 錯、不同網段，或走 TLS 但 8883 沒在聽 |
| `[NTP] 對時逾時` 之後 TLS 一直失敗 | 沒有網際網路出口。TLS 一定要正確時間，改走 `MQTT_USE_TLS 0` |
| `:8090` 看得到裝置，App 看不到 | 沒做步驟 2 的配對，或 MAC 打錯 |
| App 看得到卡片，按解鎖 30 秒逾時 | 裝置沒連上 broker，或 MAC 不一致。跑 `python tools/diagnose_devices.py` |
| 某個成員看得到別人看不到 | `/list_devices` 被多送了 `user_id`（會當成 owner 過濾），或該帳號在場域沒有角色（邀請沒接受也算） |
| 觀測台連不上 MQTT | 9001 WebSocket。`mosquitto.conf` 與 `mosquitto.dev.conf` 都有開 |

診斷指令：

```bash
python tools/diagnose_devices.py              # 兩套註冊系統的落差
python tools/diagnose_devices.py --user <帳號>  # 該帳號看得到什麼
docker logs -f iot_api_mqtt_bridge            # 指令翻譯與狀態關聯
docker logs -f mqtt_server                    # 裝置註冊
```

沒有硬體時先確認軟體側是好的：

```bash
python mqtt-server/fake_device.py <隨便一個MAC> 192.168.50.234 1883
```

---

## 五、測試帳號

密碼都是 `Test1234!`

| 帳號 | 角色 | 用途 |
| :-- | :-- | :-- |
| `itadmin047698` | 場域 4 Admin | 配對裝置、控制、管理 |
| `itmember047698` | 場域 4 Member | 驗證成員能控制、但沒有管理選單 |
| `itmember085613` | 場域 4 Guest（邀請待接受） | 驗證權限不足的提示 |

`admin001` / `member001` 的角色配置最完整（同一帳號在三個場域三種身分），
但**密碼無人知曉**（bcrypt 反推不出），要用得先改資料庫裡的雜湊值。
