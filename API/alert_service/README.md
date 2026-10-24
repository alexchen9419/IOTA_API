# MQTT 警報服務與 UC4.4 串接

啟動：docker-compose 的 `alert-service` 服務（`python -u alert_service/alert_service.py`）。Broker 連線改走共用的 `mqtt_tls`（`MQTT_PORT`／`MQTT_USE_TLS`／`MQTT_CA_CERT`，預設 TLS 8883），其餘 DB/MQTT 環境變數與預設值不變。

## 門鈴／防拆事件來源

`api-mqtt-bridge`（`control_device/mqtt_topic_bridge.py`）收到 `home/device/<mac>/event` 後，若裝置已配對就發到 `home/security/gateway_<family_id>/alert`：

| 事件 | severity | anomaly_type | 結果 |
|---|---|---|---|
| `doorbell` | LOW | 無 | 只推播 |
| `tamper_detected` | CRITICAL | `DEVICE_TAMPERING` | 記錄 UC4.4 異常 + 推播 |
| `alarm_triggered`（保險箱） | HIGH | 無 | 只推播 |

同一裝置同一事件 `ALERT_COOLDOWN_SEC`（預設 30 秒）內只轉發一次，audit_logs 照寫。

## 外部推播（Telegram / ntfy）

`common/push_notify.py`，用環境變數開關（docker compose 從 repo 根目錄 `.env` 讀，已加進 `.gitignore`）：

```
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=987654321
NTFY_TOPIC=my-home-alerts-xxxx      # 公開 ntfy.sh 的 topic 誰猜到都能訂閱，取長一點
# NTFY_URL=https://ntfy.example.com  NTFY_TOKEN=tk_...（自架/私有 topic）
```

都沒設就只走 MQTT。推播在背景執行緒送出，不阻塞 MQTT 迴圈；失敗只印 log、不重試。目前是整個系統一個目的地，不分家庭／使用者。
訂閱修正為 `home/security/+/alert`，QoS 1；MQTT 的 `+` 必須佔完整一層。
收到訊息後只接受 `home/security/gateway_<家庭整數ID>/alert`。

## Gateway 輸入

Topic：`home/security/gateway_12/alert`

```json
{
  "request_id": "GW_001_ALERT_000123",
  "gateway_id": "GW_001",
  "device_id": "ESP32_LOCK_001",
  "anomaly_type": "ILLEGAL_CONTROL_ATTEMPT",
  "severity": "HIGH",
  "timestamp": "2026-09-30T19:30:00+08:00",
  "msg": "偵測到未授權的門鎖控制請求"
}
```

`gateway_12` 中的 12 是現有 Topic 約定的 family_id，不是資料表的 gateway_id。Payload 的 `family_id` 可省略；提供時必須與 Topic 一致。Payload 必須是 JSON object；Topic、JSON 格式錯誤或家庭不一致時不記錄也不推播。

有非空 `request_id` 及 `anomaly_type` 才呼叫共用紀錄函式；同一異常重送必須沿用 request_id。HTTP 的其他選填欄位也可使用。紀錄使用 Topic family_id、`source=GATEWAY`，notification 設為 `notification_dispatched=false`、角色 ADMIN/MEMBER，因為本次推播尚未執行。原始推播 Payload 不因此改寫。

舊格式如 `{ "msg": "門鎖警報" }` 缺少任一必要欄位時會明確記錄略過原因，繼續原有通知流程；不猜測異常類型、不臨時產生 ID。

## 處理結果

| 情況 | 資料庫紀錄 | MQTT 通知 |
|---|---|---|
| 新異常且資料合法 | 三張表同一交易新增 | 嘗試通知家庭 Admin、Member |
| 相同 family/request/type 重送 | 回傳既有事件，不新增稽核或 Ledger Event | 仍重送通知 |
| 舊格式缺少 request_id 或 anomaly_type | 略過並印出原因 | 照常嘗試通知 |
| 驗證或紀錄失敗 | 回滾，印出紀錄失敗 | 仍嘗試通知 |
| Topic/JSON 格式錯誤、家庭不一致 | 無 | 無 |
| 沒有符合角色的收件人 | 先完成符合條件的異常紀錄 | 無 |

通知對象沿用 `user_families` 中同家庭的 Admin、Member，排除 Guest 與其他家庭。
輸出 Topic 保留 `app/user/{user_id}/notifications`，QoS 1；每位收件人收到原 Payload 加 `notification_target`，不共用可變的收件人欄位。

檢查 publish 回傳 rc，成功只印「已排入 MQTT 發送佇列」，不代表 App 已收到。在 on_message 內不呼叫 wait_for_publish，避免阻塞 MQTT 網路迴圈。publish 失敗不回滾已提交的異常紀錄，也不將 Ledger Payload 改成通知成功。

資料庫紀錄失敗時不會建立自動補寫工作；需要修正原因並重送相同 request_id。此版沒有增加通知去重、outbox、背景重試或 App 送達回執。服務採用同步 DB 操作；大量警報情境的非同步分流另行設計。

兩個入口必須用環境變數指向同一資料庫才能共用去重；未更改任一原始預設值。需使用前版合併 schema.sql 中既有的 security_events / audit_logs / ledger_events，這次 schema.sql 完全未改。Topic 與 family_id 一致性不是發送者身分驗證；Broker ACL 與 HTTP 認證仍由部署環境負責，此版未新增簽章驗證。
