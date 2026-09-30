# 訪客臨時令牌撤銷 API 的規範

文件依據前一版 `github%20repositories_ledger_events_completed.zip` 的同名程式。後續 `IOTA_API_guest_qr_corrected.zip` 與 `IOTA_API_alert_integrated.zip` 未包含此程式；本文件不表示已恢復部署。帳號＋QR 流程仍使用 generate_guest_qr.py / update_member_role.py。

新增文件，沿用原 Token 核發 README 風格。此 API 撤銷 guest_tokens，不是撤銷 QR 帳號式授權。

## 基本資訊

| 項目 | 內容 |
| --- | --- |
| 實作檔案 | `IOTA_API/API/control_device/revoke_guest_token.py` |
| 對應 UC | UC3.5 |
| 方法 | POST / CGI stdin；未自行檢查 REQUEST_METHOD |
| Endpoint | `/control_device/revoke_guest_token.py`（部署映射） |
| Content-Type | application/json; charset=utf-8 |
| App 互動 | 家庭現任 Admin 撤銷既有 Token |
| ESP 互動 | 無直接控制或 auth_sync MQTT 封包 |
| Ledger Event | GUEST_TOKEN_REVOKED；首次與重送成功都 HTTP 200 |

## POST Request Parameters

| 欄位 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| token_id | string | true | 內部 GT_ Token ID，不是明文 guest_token、QR_ / ROLE_ 授權 ID |
| operator_user_id | string | true（或別名） | 依序 operator_user_id、user_id、admin_uid |
| family_id | integer | false | 由 Token DB 資料決定；提供時須一致 |
| reason_code | string | false | 預設 ADMIN_MANUAL_REVOCATION，轉大寫 |
| reason | string | false | 可用 reason_detail 別名；預設 manual revocation；存 hash |

## App → API Request Packet

接受 payload 包裝或直接 object。
```json
{
  "payload": {
    "token_id": "GT_<date>_<random>",
    "operator_user_id": "admin001",
    "family_id": 12,
    "reason_code": "ADMIN_MANUAL_REVOCATION",
    "reason": "訪客離開"
  }
}
```

## Processing Rules

| 編號 | 規則 |
| --- | --- |
| 1 | SELECT guest_tokens FOR UPDATE；查無 Token 回 404。 |
| 2 | 由 Token 取家庭，檢查可選 family_id 一致。 |
| 3 | 驗證操作者當前仍為 Token 家庭 Admin；不是「只要是原核發者就能撤銷」。 |
| 4 | 已 revoked=1 回既有 Ledger，HTTP 200，不新增 audit 或事件；沒有既有 Ledger 時可為 null。 |
| 5 | 未撤銷但已自然過期或已耗盡使用次數，回 409，不另外建立撤銷事件。 |
| 6 | UPDATE revoked=1、revoked_at/by、reason code/hash，保留 Token 資料列。 |
| 7 | 寫稽核 UC3.5_GUEST_TOKEN_REVOKED 與 Ledger GUEST_TOKEN_REVOKED，全部同交易提交；失敗 rollback。 |
| 8 | remaining_uses=max(max_uses-used_count,0)，無限次 max_uses<=0 的舊 Token 為 null。 |

## API 對 SQL 寫入內容

| 資料表 | 寫入 / 更新欄位 | 觸發時機與說明 |
| --- | --- | --- |
| guest_tokens | SELECT / UPDATE revoked, revoked_at, revoked_by, revocation_reason_code, revocation_reason_hash | 不刪除 Token，不改 used_count |
| user_families | SELECT role | 驗證現任家庭 Admin |
| devices / gateways | SELECT gateway_id 等 | resolve_gateway_id 解析 Ledger 範圍 |
| audit_logs | INSERT UC3.5_GUEST_TOKEN_REVOKED | 只新增撤銷時寫 |
| ledger_events | INSERT UC3.5 GUEST_TOKEN_REVOKED / PENDING | dedup_key=UC3.5:GUEST_TOKEN_REVOKED:TOKEN:{token_id} |
| users / user_families 訪客授權 | 不更新 | QR 帳號權限須用 update_member_role.py |

## API → App Response Packet

首次撤銷 HTTP 200。
```json
{
  "status": "Success",
  "message": "Guest token revoked and ledger event queued.",
  "data": {
    "token_id": "GT_<date>_<random>",
    "revoked_at": "2026-09-30T11:30:00Z",
    "remaining_uses": 1,
    "audit_log": {
      "command_id": "AUDIT_<uuid>",
      "prev_hash": "<previous_hash>",
      "current_hash": "<sha256_hex>",
      "timestamp": "2026-09-30 11:30:00"
    },
    "ledger_event": {
      "event_id": "<uuid>",
      "dedup_key": "UC3.5:GUEST_TOKEN_REVOKED:TOKEN:GT_<date>_<random>",
      "uc_id": "UC3.5",
      "event_type": "GUEST_TOKEN_REVOKED",
      "status": "PENDING",
      "payload_hash": "<sha256_hex>",
      "ledger_reference": null,
      "created": true
    }
  }
}
```

重複撤銷 data 僅含 token_id、ledger_event；沒有新 revoked_at / remaining_uses / audit_log。

## API → ESP32 封包規範

不發 MQTT，不等待 Gateway 或 ESP32 ACK。撤銷生效依控制入口讀取 DB revoked 的策略；地端若使用快取，需另建快取失效同步。

## Error Responses

錯誤格式 `{status, code, message, detail}`。

| HTTP 狀態 | 錯誤碼 / 情境 | 說明 |
| --- | --- | --- |
| 400 | EMPTY_BODY / INVALID_JSON / INVALID_PAYLOAD / MISSING_FIELD | 本文或欄位錯誤 |
| 403 | PERMISSION_DENIED | 不是 Token 所屬家庭的現任 Admin |
| 404 | TOKEN_NOT_FOUND | Token ID 不存在 |
| 409 | TOKEN_FAMILY_MISMATCH / TOKEN_ALREADY_EXPIRED / TOKEN_USAGE_EXHAUSTED | 範圍或 Token 狀態衝突 |
| 500 | INTERNAL_ERROR | SQL 或未被分別捕捉的轉型等錯誤 |

## 注意事項

- 過期、用完及人工撤銷是不同狀態；此版不將已過期 / 用完強制轉人工撤銷。
- 不輸出或儲存明文 guest_token，也不修改 QR 帳號密碼。
- user_id 的 DB 角色檢查仍需與外層 HTTP 身分驗證綁定。
- 所有範例中的 `<uuid>`、`<sha256_hex>`、`<previous_hash>` 等為示意值；實際值由程式產生。Ledger Event 的 `PENDING` 只代表待上鏈紀錄已建立，不代表 IOTA 已確認。

## App 呼叫範例

正式部署請使用 HTTPS；以下為本機 CGI 路由示例，實際路由需與伺服器設定一致。

```bash
curl -X POST "http://localhost:8000/control_device/revoke_guest_token.py" \
  -H "Content-Type: application/json" \
  -d '{"payload":{"token_id":"GT_<date>_<random>","operator_user_id":"admin001","family_id":12,"reason_code":"ADMIN_MANUAL_REVOCATION","reason":"訪客離開"}}'
```
