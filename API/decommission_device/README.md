# 設備除役 API 與 ESP32 互動規範

## 基本資訊

| 項目 | 內容 |
| --- | --- |
| 對應檔案 | api/大概率是 API 吧/decommission_device/decommission_device.py |
| 方法 | POST |
| Endpoint | /decommission_device/decommission_device.py |
| App 互動 | Admin App 發起設備停用與除役。 |
| ESP 互動 | 正式版可由 API 透過 MQTT 下發 DECOMMISSION 命令，ESP32 回報 ACK。 |

## POST Request Parameters

| 欄位 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| auth_type | string | true | 固定 user。 |
| user_id | string | true | 執行除役的 Admin。 |
| family_id | integer | true | 設備所屬場域。 |
| device_id | string | true | 要除役的設備 ID。 |
| reason | string | false | 除役原因。 |

## App → API Request Packet

```json
{
  "payload": {
    "auth_type": "user",
    "user_id": "admin_001",
    "family_id": 12,
    "device_id": "ESP32_LOCK_001",
    "reason": "retired by owner"
  }
}
```

## Processing Rules

| 編號 | 規則 |
| --- | --- |
| 1 | 驗證 user_id 是否為該 family 的 Admin。 |
| 2 | 確認 device_id 存在且屬於 family_id。 |
| 3 | 更新 devices 狀態為 Revoked / decommissioned。 |
| 4 | 可選擇透過 MQTT 下發 DECOMMISSION 給 ESP32。 |
| 5 | 寫入 audit_logs 與必要的 control_commands。 |

## API 對 SQL 寫入內容

| 資料表 | 寫入 / 更新欄位 | 觸發時機與說明 |
| --- | --- | --- |
| devices | UPDATE status, pairing_status, revoked_at, revoked_by, revocation_reason, last_action | 除役成功後更新，阻止後續控制。 |
| audit_logs | INSERT DEVICE_DECOMMISSIONED / DEVICE_REVOKED | 保存除役稽核紀錄。 |
| control_commands | INSERT request_payload；ESP ACK 後 UPDATE response_payload/status | 若正式版透過 MQTT 對 ESP 下發除役命令。 |

## API → ESP32 Command Packet

```json
{
  "command_id": "DECOM_20260707160000_abcd1234",
  "family_id": 12,
  "device_id": "ESP32_LOCK_001",
  "action": "DECOMMISSION",
  "parameters": {
    "reason": "retired by owner"
  },
  "timestamp": "2026-07-07 16:00:00",
  "nonce": "hex_nonce"
}
```

## ESP32 → API ACK Packet

```json
{
  "command_id": "DECOM_20260707160000_abcd1234",
  "family_id": 12,
  "device_id": "ESP32_LOCK_001",
  "status": "SUCCEEDED",
  "physical_state": "DECOMMISSIONED",
  "error_code": null
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

- 除役後同一 device_id 不應再被控制 API 接受。
- 除役是安全敏感操作，正式版應要求二次確認或重新驗證。
- 若 ESP32 離線，後端仍可先標記為 Revoked，再等待設備下一次上線同步。

## App 呼叫範例

App 端以 HTTPS POST 送出 JSON；本機測試可用 curl 或 printf 對應 CGI stdin。

```http
POST /decommission_device/decommission_device.py
Content-Type: application/json; charset=utf-8
```

```json
{
  "payload": {
    "auth_type": "user",
    "user_id": "admin_001",
    "family_id": 12,
    "device_id": "ESP32_LOCK_001",
    "reason": "retired by owner"
  }
}
```

```bash
curl -X POST "http://localhost:8000/decommission_device/decommission_device.py" \
  -H "Content-Type: application/json" \
  -d '{"payload": {"auth_type": "user", "user_id": "admin_001", "family_id": 12, "device_id": "ESP32_LOCK_001", "reason": "retired by owner"}}'
```
