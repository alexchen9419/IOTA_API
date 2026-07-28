# 設備狀態回報 API 的規範

## 基本資訊

| 項目 | 內容 |
| --- | --- |
| 實作檔案 | api/大概率是 API 吧/control_device/device_status_update.py |
| 方法 | POST / CGI stdin |
| Endpoint | /control_device/device_status_update.py |
| Content-Type | application/json; charset=utf-8 |
| App 互動 | 一般不由 App 呼叫；App 透過儀表板查詢 API 讀取結果。 |
| ESP 互動 | ESP32 或 MQTT Worker 回報命令執行狀態。 |

## POST Request Parameters

| 欄位 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| command_id | string | false | 對應控制命令；開機/心跳可為 BOOT。 |
| family_id | integer | true | 設備所屬場域 ID。 |
| device_id | string | true | ESP32 設備 ID。 |
| status | string | true | SUCCEEDED、FAILED、TIMEOUT。 |
| physical_state | string | false | LOCKED、UNLOCKED、ON、OFF 等。 |
| battery | integer | false | 電量百分比；無電池可固定 100 或 NULL。 |
| rssi | integer | false | Wi-Fi RSSI dBm。 |
| error_code | string | false | 失敗時的設備錯誤碼。 |

## ESP32 → API Request Packet

```json
{
  "payload": {
    "command_id": "CMD_20260707151855_067a1154",
    "family_id": 12,
    "device_id": "ESP32_LOCK_001",
    "status": "SUCCEEDED",
    "physical_state": "UNLOCKED",
    "battery": 87,
    "rssi": -52,
    "error_code": null
  }
}
```

## Processing Rules

| 編號 | 規則 |
| --- | --- |
| 1 | 檢查 family_id、device_id、status。 |
| 2 | 檢查 device 是否存在；若不存在回 DEVICE_NOT_FOUND。 |
| 3 | 若 command_id 對應 control_commands，更新 status、response_payload、completed_at。 |
| 4 | 新增 device_telemetry，telemetry_data 保存完整狀態封包。 |
| 5 | 寫入 audit_logs，記錄設備狀態回報事件。 |
| 6 | 回傳更新結果給測試者或 Worker。 |

## API 對 SQL 寫入內容

| 資料表 | 寫入 / 更新欄位 | 觸發時機與說明 |
| --- | --- | --- |
| control_commands | UPDATE status, response_payload, completed_at | 當 payload.command_id 能對應命令時更新。 |
| device_telemetry | INSERT family_id, device_id, command_id, status, physical_state, telemetry_data, battery, rssi, recorded_at | 每次有效狀態回報都新增一筆歷史紀錄。 |
| audit_logs | INSERT DEVICE_STATUS 事件，raw_data 保存 ESP 狀態封包 | 用於狀態回報稽核與故障追蹤。 |

## API → App / Worker Response Packet

```json
{
  "status": "Success",
  "message": "Device status updated.",
  "data": {
    "command_id": "CMD_...",
    "device_id": "ESP32_LOCK_001",
    "status": "SUCCEEDED",
    "physical_state": "UNLOCKED"
  }
}
```

## Error Responses

| HTTP 狀態 | 錯誤碼 | 說明 |
| --- | --- | --- |
| 400 | INVALID_JSON / MISSING_FIELD | JSON 格式錯誤或缺少必要欄位。 |
| 403 | ROLE_DENIED / TOKEN_FORMAT_INVALID / TOKEN_EXPIRED / TOKEN_USED_UP | 角色不符、訪客令牌格式錯誤、過期或次數耗盡。 |
| 404 | DEVICE_NOT_FOUND / FAMILY_NOT_FOUND / TOKEN_NOT_FOUND | 查無設備、場域或令牌。 |
| 409 | DEVICE_REVOKED / DUPLICATE_RECORD | 設備狀態衝突或不可重複操作。 |
| 500 | DB_DRIVER_MISSING / INTERNAL_ERROR | 資料庫驅動、SQL 或伺服器內部錯誤。 |

## 注意事項

- 此 API 是 ESP/Gateway 回報入口，不建議讓一般 App 使用者直接呼叫。
- recorded_at 由資料庫產生；ESP 端若提供 device_time，可放在 telemetry_data 內。
- Dashboard 的 connection_health 依此 API 寫入的最新 telemetry 判斷。

## 測試呼叫範例

App 端以 HTTPS POST 送出 JSON；本機測試可用 curl 或 printf 對應 CGI stdin。

```http
POST /control_device/device_status_update.py
Content-Type: application/json; charset=utf-8
```

```json
{
  "payload": {
    "command_id": "CMD_20260707151855_067a1154",
    "family_id": 12,
    "device_id": "ESP32_LOCK_001",
    "status": "SUCCEEDED",
    "physical_state": "UNLOCKED",
    "battery": 87,
    "rssi": -52,
    "error_code": null
  }
}
```

```bash
curl -X POST "http://localhost:8000/control_device/device_status_update.py" \
  -H "Content-Type: application/json" \
  -d '{"payload": {"command_id": "CMD_20260707151855_067a1154", "family_id": 12, "device_id": "ESP32_LOCK_001", "status": "SUCCEEDED", "physical_state": "UNLOCKED", "battery": 87, "rssi": -52, "error_code": null}}'
```
