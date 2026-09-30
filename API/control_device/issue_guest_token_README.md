# 訪客臨時令牌核發 API 的規範

文件依據前一版 `github%20repositories_ledger_events_completed.zip` 的同名程式。後續 `IOTA_API_guest_qr_corrected.zip` 與 `IOTA_API_alert_integrated.zip` 未包含此程式；本文件不表示已恢復部署。帳號＋QR 流程仍使用 generate_guest_qr.py / update_member_role.py。

以原 `control_device/issue_guest_token_demo_README.md` 的章節與表格為基礎，改為正式 Token API 的實際規範；Demo 文件及程式不因此被替換。

## 基本資訊

| 項目 | 內容 |
| --- | --- |
| 實作檔案 | `IOTA_API/API/control_device/issue_guest_token.py` |
| 對應 UC | UC3.4 |
| 方法 | POST / CGI stdin；程式讀 stdin，未自行檢查 REQUEST_METHOD |
| Endpoint | `/control_device/issue_guest_token.py`（部署映射） |
| Content-Type | application/json; charset=utf-8 |
| App 互動 | 場域 Admin 核發指定設備的 GUEST_ 令牌 |
| ESP 互動 | 無直接封包；後續交由 control_device.py 驗證令牌並控制設備 |
| Ledger Event | GUEST_TOKEN_ISSUED；新增成功 HTTP 201 |

## POST Request Parameters

| 欄位 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| created_by | string | true（或別名） | 核發者；依序讀 created_by、user_id、admin_uid |
| family_id | integer | true | 設備及 Admin 所屬場域 |
| device_id | string | true | 指定設備 |
| allowed_actions | array[string] / string | false | 預設 ["UNLOCK"]；轉大寫、去重及排序 |
| expires_at | string | false | ISO 8601；提供時優先於 expires_in_minutes；無時區視為 UTC |
| expires_in_minutes | integer | false | 預設 10，必須 > 0 |
| max_uses | integer | false | 預設 1，必須 > 0 |

## App → API Request Packet

接受 payload 包裝或直接 JSON object。
```json
{
  "payload": {
    "created_by": "admin001",
    "family_id": 12,
    "device_id": "ESP32_LOCK_001",
    "allowed_actions": [
      "UNLOCK"
    ],
    "expires_in_minutes": 10,
    "max_uses": 1
  }
}
```

## Processing Rules

| 編號 | 規則 |
| --- | --- |
| 1 | 驗證家庭 Admin 角色；不能只因 created_by 存在就核發。 |
| 2 | allowed_actions 僅接受 LOCK、UNLOCK、ON、OFF、OPEN、CLOSE、TOGGLE、START、STOP。 |
| 3 | 期限必須晚於目前 UTC；GUEST_TOKEN_MAX_MINUTES 預設 1440，設定大於 0 時限制最長期限。 |
| 4 | 檢查設備存在、家庭一致且 status 不為 revoked / retired / decommissioned / disabled。 |
| 5 | 產生 GT_ Token ID 和 GUEST_ 明文令牌，DB 只存 SHA-256 token_hash。 |
| 6 | 同一 transaction 寫 guest_tokens、audit_logs、ledger_events；失敗 rollback，成功 commit。 |
| 7 | 每次核發會產生新 Token；本入口沒有依 request_id 去重，重送可能核發多組。 |

## API 對 SQL 寫入內容

| 資料表 | 寫入 / 更新欄位 | 觸發時機與說明 |
| --- | --- | --- |
| user_families | SELECT role | 驗證核發者為目標家庭 Admin |
| devices | SELECT device_id, family_id, gateway_id, status | 驗證設備範圍及狀態 |
| guest_tokens | INSERT token_id, token_hash, family_id, device_id, allowed_actions, expires_at, used_count=0, max_uses, revoked=0, revoked_at/by=NULL, revocation_reason_code/hash=NULL, created_by, created_at | 不儲存 guest_token 明文 |
| audit_logs | INSERT UC3.4_GUEST_TOKEN_ISSUED 及鏈式稽核欄位 | 使用 common/audit_log_service.py |
| ledger_events | INSERT UC3.4 / GUEST_TOKEN_ISSUED / payload / payload_hash / PENDING | dedup_key 依 Token ID |

## API → App Response Packet

新增成功 HTTP 201。
```json
{
  "status": "Success",
  "message": "Guest token issued and ledger event queued.",
  "data": {
    "token_id": "GT_<date>_<random>",
    "guest_token": "GUEST_<random>",
    "family_id": 12,
    "device_id": "ESP32_LOCK_001",
    "allowed_actions": [
      "UNLOCK"
    ],
    "valid_from": "2026-09-30T11:30:00Z",
    "expires_at": "2026-09-30T11:40:00Z",
    "max_uses": 1,
    "audit_log": {
      "command_id": "AUDIT_<uuid>",
      "prev_hash": "<previous_hash>",
      "current_hash": "<sha256_hex>",
      "timestamp": "2026-09-30 11:30:00"
    },
    "ledger_event": {
      "event_id": "<uuid>",
      "dedup_key": "UC3.4:GUEST_TOKEN_ISSUED:TOKEN:GT_<date>_<random>",
      "uc_id": "UC3.4",
      "event_type": "GUEST_TOKEN_ISSUED",
      "status": "PENDING",
      "payload_hash": "<sha256_hex>",
      "ledger_reference": null,
      "created": true
    },
    "note": "Plaintext guest_token is returned only in this successful issuance response."
  }
}
```

## API → ESP32 封包規範

| 方向 | 規範 |
| --- | --- |
| API → ESP32 | 不直接傳送，不發布 MQTT auth_sync |
| 後續控制 | control_device.py 使用 auth_type=guest_token 與 guest_token 明文；token_id 不是控制憑證 |

## Error Responses

錯誤本文為 `{status, code, message, detail}`。部分 int() 型別轉換錯誤目前落入 500，不宣稱全部輸入錯誤都有 400。

| HTTP 狀態 | 錯誤碼 / 情境 | 說明 |
| --- | --- | --- |
| 400 | EMPTY_BODY / INVALID_JSON / INVALID_PAYLOAD / MISSING_FIELD | 空本文、格式錯誤或缺欄位 |
| 400 | INVALID_ALLOWED_ACTIONS / UNSUPPORTED_ACTION | 動作格式錯誤或非白名單 |
| 400 | INVALID_MAX_USES / INVALID_EXPIRES_AT / INVALID_EXPIRY / EXPIRY_TOO_LONG | 次數或期限不合法 |
| 403 | PERMISSION_DENIED | 非家庭 Admin |
| 404 | DEVICE_NOT_FOUND | 設備不存在 |
| 409 | DEVICE_FAMILY_MISMATCH / DEVICE_INACTIVE | 設備範圍不符或已停用 |
| 500 | INTERNAL_ERROR | SQL、未被個別捕捉的型別轉換或其他內部錯誤 |

## 注意事項

- 明文 guest_token 只在核發成功回應顯示，DB / audit / ledger 不寫明文。
- 不建立 users / user_families 訪客帳號；不能把 GT_ Token ID 或 guest_token 當成 QR_ authorization_id。
- 驗證的是傳入 user_id 的資料庫角色；此檔未提供 Bearer Token 或密碼驗證，HTTP 身分綁定需由外層完成。
- 所有範例中的 `<uuid>`、`<sha256_hex>`、`<previous_hash>` 等為示意值；實際值由程式產生。Ledger Event 的 `PENDING` 只代表待上鏈紀錄已建立，不代表 IOTA 已確認。

## App 呼叫範例

正式部署請使用 HTTPS；以下為本機 CGI 路由示例，實際路由需與伺服器設定一致。

```bash
curl -X POST "http://localhost:8000/control_device/issue_guest_token.py" \
  -H "Content-Type: application/json" \
  -d '{"payload":{"created_by":"admin001","family_id":12,"device_id":"ESP32_LOCK_001","allowed_actions":["UNLOCK"],"expires_in_minutes":10,"max_uses":1}}'
```
