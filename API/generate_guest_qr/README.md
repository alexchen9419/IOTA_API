# 產生臨時訪客 QR Code 通行證 API 的規範

依原 `System_API_Specifications.md`「六、產生臨時訪客 QR Code 通行證 API」擴充為獨立 README，沿用其他 API 的表格與封包章節。

## 基本資訊

| 項目 | 內容 |
| --- | --- |
| 實作檔案 | `IOTA_API/API/generate_guest_qr/generate_guest_qr.py` |
| 對應 UC | UC3.4，帳號＋QR 授權 |
| 方法 | POST；檢查 REQUEST_METHOD，非 POST 回 405 |
| Endpoint | `/cgi-bin/generate_guest_qr.py`（沿用原文件部署映射） |
| Content-Type | application/json; charset=utf-8 |
| App 互動 | 取得 guest 帳號、一次回傳的密碼及 QR URL；QR 圖由前端產生 |
| Gateway 互動 | DB commit 後發布 MEMBER_ROLE_CHANGED 到 auth_sync |
| Ledger Event | GUEST_TOKEN_ISSUED，credential_kind=GUEST_ACCOUNT_QR |

## POST Request Parameters

| 欄位 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| family_id | integer | true | 正整數家庭 ID |
| admin_uid | string | true | 此家庭 Admin |
| start_time | string / null | false | ISO 8601 或 YYYY-MM-DD HH:MM:SS，省略為目前台灣時間 |
| end_time | string / null | 條件式 | 與 max_uses 至少一個非 null；期限須晚於 start_time 與目前時間 |
| max_uses | integer / null | 條件式 | 正整數，不接受 0、負數、bool、1.5；與 end_time 至少提供一個 |

## App → API Request Packet

接受 payload 包裝或直接 object。測試期限請設定為執行時仍在未來的日期。
```json
{
  "payload": {
    "family_id": 12,
    "admin_uid": "admin001",
    "start_time": "2030-01-01 15:00:00",
    "end_time": "2030-01-02 23:59:59",
    "max_uses": 3
  }
}
```

## Processing Rules

| 編號 | 規則 |
| --- | --- |
| 1 | 檢查 POST、JSON、family_id、Admin、授權至少有限期或限次。 |
| 2 | 無時區時間視為台灣 UTC+8；帶時區時間轉成台灣本地時間存 DB；Ledger 轉成 UTC Z。 |
| 3 | 鎖定家庭與 Admin 角色；家庭不存在 404，非 Admin 403。 |
| 4 | 在同家庭尋找 Active、guest_ 開頭且 Revoked 或已過期的閒置帳號；其他家庭仍有未撤銷關係的帳號不重用。 |
| 5 | 無可重用帳號則新建 guest_ + token_hex(8) 帳號；新建或重用都產生 secrets.token_urlsafe(24) 密碼，DB 存 Bcrypt。 |
| 6 | 每次核發產生 QR_<uuid> authorization_id，存 user_families.guest_grant_id；不是 guest_tokens.token_id。 |
| 7 | users / user_families / audit_logs / ledger_events 同交易提交；Ledger 與 audit 不存明文密碼或 control_url。 |
| 8 | commit 後同步 MQTT；失敗仍回 200 與 mqtt_published=false，不回滾已核發授權。 |
| 9 | 回傳 control_url 供前端製作 QR；GUEST_QR_BASE_URL 預設為示例網址，實際 QR 控制頁需要另行部署。 |

## API 對 SQL 寫入內容

| 資料表 | 寫入 / 更新欄位 | 觸發時機與說明 |
| --- | --- | --- |
| families / user_families | SELECT 家庭、Admin；UPDATE / INSERT 訪客關係 role=Guest, start_time, end_time, max_uses, guest_grant_id；重用時 status=NULL | 授權以家庭為範圍 |
| users | INSERT user_id, username, email, phone_number, password_hash；重用時 UPDATE password_hash | 密碼 Bcrypt；不產生 guest_token |
| audit_logs | INSERT UC3.4_GUEST_TOKEN_ISSUED | parameters 含 authorization_id、guest hash、期限、次數、credential_kind |
| ledger_events | INSERT UC3.4 GUEST_TOKEN_ISSUED / PENDING | dedup_key=UC3.4:GUEST_TOKEN_ISSUED:GRANT:{authorization_id} |
| guest_tokens | 不寫入 | QR 帳號流程與 GUEST_ Token 流程不同 |

## API → App Response Packet

HTTP 200；保留原 user_id、password、control_url，增加授權資料及事件 metadata。範例 msg 對應新建帳號分支。
```json
{
  "status": "Success",
  "msg": "已建立全新訪客帳號 (guest_<random>)。權限異動已送交 MQTT broker；Gateway 套用狀態需另行確認",
  "data": {
    "user_id": "guest_<random>",
    "password": "<new_random_password>",
    "control_url": "https://your-domain.com/qr-control?uid=guest_<random>&pwd=<urlencoded_password>",
    "start_time": "2030-01-01 15:00:00",
    "end_time": "2030-01-02 23:59:59",
    "max_uses": 3,
    "authorization_id": "QR_<uuid>",
    "audit_log": {
      "command_id": "AUDIT_<uuid>",
      "prev_hash": "<previous_hash>",
      "current_hash": "<sha256_hex>",
      "timestamp": "2026-09-30 11:30:00"
    },
    "ledger_event": {
      "event_id": "<uuid>",
      "dedup_key": "UC3.4:GUEST_TOKEN_ISSUED:GRANT:QR_<uuid>",
      "uc_id": "UC3.4",
      "event_type": "GUEST_TOKEN_ISSUED",
      "status": "PENDING",
      "payload_hash": "<sha256_hex>",
      "ledger_reference": null,
      "created": true
    },
    "mqtt_published": true
  }
}
```

## API → Gateway / ESP32 封包規範

Topic：`home/security/gateway_{family_id}/auth_sync`，QoS 1，使用 mqtt_tls 的 MQTT_PORT / MQTT_USE_TLS / MQTT_CA_CERT 設定；預設 port 8883、TLS 開啟。
```json
{
  "event": "MEMBER_ROLE_CHANGED",
  "user_data": {
    "user_id": "guest_<random>",
    "role": "Guest",
    "start_time": "2030-01-01 15:00:00",
    "end_time": "2030-01-02 23:59:59",
    "max_uses": 3
  }
}
```

封包不含密碼或 authorization_id。本 API 不直接控制 ESP32，不等待 Gateway 套用 ACK；mqtt_published=true 不能證明地端已更新。

## Error Responses

| HTTP 狀態 | 錯誤碼 / 情境 | 說明 |
| --- | --- | --- |
| 400 | 空本文 / JSON / 缺欄位 / 時間及次數不合法 | 本文 status=Error、msg |
| 403 | 不是該家庭 Admin | 拒絕核發 |
| 404 | 家庭不存在 | 找不到指定場域 |
| 405 | 非 POST | 必須設定 REQUEST_METHOD=POST |
| 500 | DB / 其他內部錯誤 | 提示確認最新 schema.sql |

## 注意事項

- 每次核發都是新授權，重送不去重，會輪換密碼及產生新的 authorization_id。
- 僅設定 max_uses 時 end_time 可為 null；設定期限時 max_uses 可為 null。
- API 存授權限制，Gateway 實際驗證及扣次仍需整合；不能以 Ledger 入庫宣稱控制流程已完成。
- control_url 含登入憑證，前端及伺服器避免記錄完整 URL；本檔不生成 PNG QR 圖。
- user_id / admin_uid 必須對應真實資料列；角色查驗尚未綁定 HTTP 登入身分。
- 所有範例中的 `<uuid>`、`<sha256_hex>`、`<previous_hash>` 等為示意值；實際值由程式產生。Ledger Event 的 `PENDING` 只代表待上鏈紀錄已建立，不代表 IOTA 已確認。

## App 呼叫範例

正式部署請使用 HTTPS；以下為本機 CGI 路由示例，實際路由需與伺服器設定一致。

```bash
curl -X POST "http://localhost:8000/cgi-bin/generate_guest_qr.py" \
  -H "Content-Type: application/json" \
  -d '{"payload":{"family_id":12,"admin_uid":"admin001","start_time":"2030-01-01 15:00:00","end_time":"2030-01-02 23:59:59","max_uses":3}}'
```

直接執行 CGI 時請設定 REQUEST_METHOD=POST。
