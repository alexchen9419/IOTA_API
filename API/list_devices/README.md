# 設備清單查詢 API 的規範

## 基本資訊

| 項目 | 內容 |
| --- | --- |
| 對應檔案 | api/大概率是 API 吧/list_device/list_devices.py |
| 方法 | POST / GET 依原實作 |
| Endpoint | /list_device/list_devices.py |
| App 互動 | App 查詢使用者在指定場域可見的設備清單。 |
| ESP 互動 | 無直接 ESP 封包；設備狀態來源為 devices 與 device_telemetry。 |

## POST Request Parameters

| 欄位 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| auth_type | string | false | 若有 token 流程則固定 user。 |
| user_id | string | true | 查詢者 ID。 |
| family_id | integer | true | 欲查詢場域。 |

## App → API Request Packet

```json
{
  "payload": {
    "auth_type": "user",
    "user_id": "admin_001",
    "family_id": 12
  }
}
```

## Processing Rules

| 編號 | 規則 |
| --- | --- |
| 1 | 確認 user_id 在 family_id 中具備可見設備權限。 |
| 2 | 查詢 devices 表中的場域設備。 |
| 3 | 可選擇 join 最新 device_telemetry 取得狀態。 |
| 4 | 回傳設備清單給 App。 |

## API 對 SQL 寫入內容

| 資料表 | 寫入 / 更新欄位 | 觸發時機與說明 |
| --- | --- | --- |
| user_families | SELECT role | 判斷是否可看該場域設備。 |
| devices | SELECT device_id, device_name, device_type, status, pairing_status, gateway_id | 回傳設備主資料。 |
| device_telemetry | SELECT latest telemetry per device | 若實作最新狀態顯示，讀取但不寫入。 |
| audit_logs | 可 INSERT DEVICE_LIST_VIEWED | 正式版可記錄設備清單查詢。 |

## API → App Response Packet

```json
{
  "status": "Success",
  "data": {
    "family_id": 12,
    "devices": [
      {
        "device_id": "ESP32_LOCK_001",
        "device_name": "UC4 測試門鎖",
        "device_type": "smart_lock",
        "status": "Active",
        "pairing_status": "paired",
        "physical_state": "UNLOCKED",
        "connection_health": "GOOD"
      }
    ]
  }
}
```

## API ↔ ESP32 封包關係

| 方向 | 規範 |
| --- | --- |
| API → ESP32 | 本 API 不下發控制封包。 |
| ESP32 → API | 設備狀態由 status topic 或狀態回報 API 更新。 |
| API → App | 回傳資料庫中的可見設備與最新狀態。 |

## Error Responses

| HTTP 狀態 | 錯誤碼 | 說明 |
| --- | --- | --- |
| 400 | INVALID_JSON / MISSING_FIELD | JSON 格式錯誤或缺少必要欄位。 |
| 403 | ROLE_DENIED / TOKEN_FORMAT_INVALID / TOKEN_EXPIRED / TOKEN_USED_UP | 角色不符、訪客令牌格式錯誤、過期或次數耗盡。 |
| 404 | DEVICE_NOT_FOUND / FAMILY_NOT_FOUND / TOKEN_NOT_FOUND | 查無設備、場域或令牌。 |
| 409 | DEVICE_REVOKED / DUPLICATE_RECORD | 設備狀態衝突或不可重複操作。 |
| 500 | DB_DRIVER_MISSING / INTERNAL_ERROR | 資料庫驅動、SQL 或伺服器內部錯誤。 |

## 注意事項

- 此 API 是查詢功能，不應造成 ESP32 作動。
- App 切換場域時可先呼叫此 API 或 Dashboard API 同步設備清單。

## App 呼叫範例

App 端以 HTTPS POST 送出 JSON；本機測試可用 curl 或 printf 對應 CGI stdin。

```http
POST /list_device/list_devices.py
Content-Type: application/json; charset=utf-8
```

```json
{
  "payload": {
    "auth_type": "user",
    "user_id": "admin_001",
    "family_id": 12
  }
}
```

```bash
curl -X POST "http://localhost:8000/list_device/list_devices.py" \
  -H "Content-Type: application/json" \
  -d '{"payload": {"auth_type": "user", "user_id": "admin_001", "family_id": 12}}'
```
