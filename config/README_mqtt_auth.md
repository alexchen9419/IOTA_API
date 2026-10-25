# MQTT 帳號密碼與 ACL

broker 已關閉匿名（`allow_anonymous false`），用 `passwd` + `acl` 控管。

| 帳號 | 誰在用 | 權限（細節見 `acl`） |
|---|---|---|
| `server` | API、api-mqtt-bridge、alert-service、mqtt-server、mqtt-monitor | `home/#`、`app/#` 讀寫 |
| `nodered` | Node-RED | 讀 state/event/register，只寫 `home/device/+/cmd` |
| `device` | ESP32 韌體、`fake_device.py` | 只寫 register/state/event；只讀自己的 config/cmd/ota（**不能寫 cmd**） |

## 初次設定 / 換密碼

```powershell
.\config\gen_mqtt_users.ps1                       # 缺的密碼寫進 .env，重建 config/passwd
docker compose up -d --force-recreate mqtt-broker # 載入新的 passwd / acl
docker compose up -d                              # 其他服務帶新密碼重啟
```

要換某個密碼：從 `.env` 刪掉那一行，重跑腳本，再重啟對應服務。

- 密碼只存在 `.env`（不進版控）與雜湊後的 `config/passwd`（不進版控）。
- **ESP32 韌體**：把 `.env` 的 `MQTT_DEVICE_PASSWORD` 填進 `MqttSmartLock.ino` 的 `MQTT_PASS`，重新燒錄。
- **Node-RED**：登入編輯器（帳號 `admin`，密碼 `.env` 的 `NODE_RED_ADMIN_PASSWORD`），
  開 "Mosquitto (TLS)" broker 節點 → Security 分頁，帳號 `nodered`，密碼 `.env` 的
  `MQTT_NODERED_PASSWORD` → Deploy。（Node-RED 把連線帳密加密存在 `flows_cred.json`，無法從環境變數帶入。）

## 已知限制

- 所有裝置共用一組 `device` 帳號：偷到它不能下指令，但可以偽造任何裝置的 state/event。
  要做到「一台一組」需要註冊流程發帳號（ACL 可改成 `pattern` + `%u`）。
- `mosquitto.dev.conf`（明文 1883 / WebSocket 9001）同樣要帳密，但沒有 TLS，只准本機開發用。
