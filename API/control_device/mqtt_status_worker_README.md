# MQTT Worker 與 ESP32 互動規範

## 基本資訊

| 項目 | 內容 |
| --- | --- |
| 實作檔案 | api/大概率是 API 吧/control_device/mqtt_status_worker.py |
| 執行位置 | Gateway / Raspberry Pi / Ubuntu 測試環境 |
| 用途 | 訂閱 ESP32 狀態 topic，將 MQTT 狀態封包轉交設備狀態回報 API 的處理函式。 |
| App 互動 | 無直接 App 封包。 |
| ESP 互動 | 訂閱 home/+/device/+/status。 |

## ESP32 → MQTT Worker 封包規範

| 項目 | 內容 |
| --- | --- |
| Topic Filter | home/+/device/+/status |
| Topic Example | home/12/device/ESP32_LOCK_001/status |
| Payload Format | JSON object，內容同設備狀態回報 API。 |
| QoS | 建議 1 |

```json
{
  "command_id": "CMD_20260707151855_067a1154",
  "family_id": 12,
  "device_id": "ESP32_LOCK_001",
  "status": "SUCCEEDED",
  "physical_state": "UNLOCKED",
  "battery": 87,
  "rssi": -52,
  "error_code": null
}
```

## Processing Rules

| 編號 | 規則 |
| --- | --- |
| 1 | 啟動後連線 MQTT Broker。 |
| 2 | 訂閱 MQTT_STATUS_TOPIC，預設 home/+/device/+/status。 |
| 3 | 收到 ESP32 狀態封包後解析 JSON。 |
| 4 | 由 topic 補入 family_id 與 device_id。 |
| 5 | 呼叫 device_status_update.handle_status(payload) 寫入資料庫。 |

## API 對 SQL 寫入內容

| 資料表 | 寫入 / 更新欄位 | 觸發時機與說明 |
| --- | --- | --- |
| control_commands | UPDATE status, response_payload, completed_at | 透過 device_status_update 轉寫。 |
| device_telemetry | INSERT telemetry_data、physical_state、battery、rssi | 每次 ESP status topic 進來後新增。 |
| audit_logs | INSERT DEVICE_STATUS 或相應事件 | 保存狀態回報稽核紀錄。 |

## 環境變數

| 變數 | 預設值 | 說明 |
| --- | --- | --- |
| MQTT_HOST | localhost | MQTT Broker 位址。 |
| MQTT_PORT | 1883 | Broker Port。 |
| MQTT_USERNAME / MQTT_PASSWORD | 空 | Broker 帳密。 |
| MQTT_USE_TLS | 0 | 是否啟用 TLS。 |
| MQTT_STATUS_TOPIC | home/+/device/+/status | 狀態訂閱 topic。 |

## 注意事項

- Worker 必須常駐執行，否則 mqtt 模式下 ESP32 回報不會寫入 DB。
- 若使用 mock 模式，可不啟動 Worker。
- 正式版建議 MQTT Broker 啟用帳密與 TLS，並在 payload 加入 HMAC。

## 啟動範例

```bash
export MQTT_HOST=localhost
export MQTT_PORT=1883
python -u api/大概率是 API 吧/control_device/mqtt_status_worker.py
```
