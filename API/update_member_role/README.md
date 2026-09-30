# 更新成員權限 API 與 Gateway 互動規範

依原 `System_API_Specifications.md`「五、更新成員權限 API」更新，擴充為同風格獨立 README。保留原輸入欄位，更新 Ledger 分流與重複撤銷回應。

## 基本資訊

| 項目 | 內容 |
| --- | --- |
| 實作檔案 | `IOTA_API/API/update_member_role/update_member_role.py` |
| 對應 UC | UC3.3 成員撤銷、UC3.4 Guest 授權、UC3.5 Guest 撤銷 |
| 方法 | POST；非 POST 回 405 |
| Endpoint | `/cgi-bin/update_member_role.py`（沿用原文件部署映射） |
| App 互動 | 家庭 Admin 變更同家庭成員角色或撤銷權限 |
| Gateway 互動 | commit 後 auth_sync，無設備控制封包或套用 ACK |
| 成功狀態 | HTTP 200；重複撤銷也為 Success / 200 |

## POST Request Parameters

| 欄位 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| family_id | integer | true | 正整數家庭 ID |
| admin_uid | string | true | 操作的家庭 Admin |
| target_uid | string | true | 平台已註冊且 Active 的目標帳號 |
| target_role | string | true | 大小寫須符合 Admin / Member / Guest / Technician / SP / Revoked |
| start_time | string / null | false | 非撤銷分支寫入；Guest Ledger 無時區視 UTC+8 |
| end_time | string / null | false | 非撤銷分支寫入；撤銷時使用 DB NOW()，忽略呼叫端 end_time |
| max_uses | integer / null | false | 非撤銷分支寫入；本入口沒有與 QR API 相同的正數 / 有界授權完整驗證 |
| reason_code | string | false | 撤銷時使用；Guest 預設 ADMIN_MANUAL_REVOCATION，其他預設 ADMIN_REMOVED_MEMBER；大寫、最多 64 字元 |
| reason | string | false | 撤銷原因，Ledger 記雜湊 |

## App → API Request Packet

接受 payload 包裝或直接 object；本例撤銷帳號式 Guest，不是撤銷 guest_tokens。
```json
{
  "payload": {
    "family_id": 12,
    "admin_uid": "admin001",
    "target_uid": "guest_example",
    "target_role": "Revoked",
    "reason_code": "ADMIN_MANUAL_REVOCATION",
    "reason": "訪客離開"
  }
}
```

## Processing Rules

| 編號 | 規則 |
| --- | --- |
| 1 | 鎖定家庭和操作 / 目標的 user_families；操作者必須 Admin。 |
| 2 | 不可將自己的角色改成非 Admin；目標必須是已註冊且 Active 的 users。 |
| 3 | Revoked 分支要求目標原本屬於此家庭，撤銷唯一 Admin 回 409。 |
| 4 | 角色撤銷設 role=Revoked、end_time=NOW()；影響此家庭，不是全域停用 users。 |
| 5 | 原角色 Guest 時建立 UC3.5 GUEST_TOKEN_REVOKED：沿用 guest_grant_id，舊 Guest 無 ID 則產生 LEGACY_ 授權 ID。 |
| 6 | 原角色非 Guest 時建立 UC3.3 MEMBER_PERMISSION_REVOKED。 |
| 7 | 已 Revoked 重送回 Success，取既有 Ledger，不重複稽核或事件，但仍重送 MQTT；舊資料沒有 Ledger 時可回 null。 |
| 8 | 設定 Guest 時每次產生 ROLE_<uuid> 授權及 UC3.4 GUEST_TOKEN_ISSUED；不建立新 users 或密碼、不生成 QR。 |
| 9 | 設定其他非 Revoked 角色只 Upsert 關係；目前不為該分支新增 audit / Ledger，清空 guest_grant_id。 |
| 10 | DB、需要的稽核與事件同交易提交；MQTT 發布在 commit 後，失敗保留 DB 成功。 |

## API 對 SQL 寫入內容

| 資料表 | 寫入 / 更新欄位 | 觸發時機與說明 |
| --- | --- | --- |
| families | SELECT id FOR UPDATE | 確保家庭存在 |
| users | SELECT status | 目標需存在且 Active |
| user_families | SELECT / INSERT / UPDATE role, start_time, end_time, max_uses, guest_grant_id | 撤銷改 role/end_time；其他身分 Upsert |
| audit_logs | INSERT UC3.3_MEMBER_PERMISSION_REVOKED / UC3.5_GUEST_TOKEN_REVOKED / UC3.4_GUEST_TOKEN_ISSUED | 只在新撤銷或設定 Guest 分支 |
| ledger_events | INSERT UC3.3 / UC3.4 / UC3.5 對應事件 | 與業務更新同交易；一般角色更新沒有事件 |
| guest_tokens | 不寫入 | 不撤銷 GUEST_ 明文令牌 |

## Ledger Event 與去重規則

| 操作 | dedup_key / credential_kind |
| --- | --- |
| QR Guest 撤銷 | UC3.5:GUEST_TOKEN_REVOKED:GRANT:{QR_id}；GUEST_ACCOUNT_QR |
| 角色 Guest 撤銷 | UC3.5:GUEST_TOKEN_REVOKED:GRANT:{ROLE_id}；GUEST_ACCOUNT_ROLE |
| 舊 Guest 撤銷 | 先產生 LEGACY_ ID，後續重送沿用；LEGACY_GUEST_ACCOUNT |
| 設定 Guest | UC3.4:GUEST_TOKEN_ISSUED:GRANT:{new_ROLE_id}；每次是新授權 |
| 非 Guest 撤銷 | UC3.3:MEMBER_PERMISSION_REVOKED:FAMILY:{family_id}:MEMBER:{target_hash_24}:AT:{revocation_time} |

## API → App Response Packet

本例為首次撤銷 QR 帳號授權，HTTP 200。
```json
{
  "status": "Success",
  "msg": "已成功撤銷該使用者所有權限 (變更為 Revoked)。身分異動已送交 MQTT broker；Gateway 套用狀態需另行確認",
  "data": {
    "mqtt_published": true,
    "ledger_event": {
      "event_id": "<uuid>",
      "dedup_key": "UC3.5:GUEST_TOKEN_REVOKED:GRANT:QR_<uuid>",
      "uc_id": "UC3.5",
      "event_type": "GUEST_TOKEN_REVOKED",
      "status": "PENDING",
      "payload_hash": "<sha256_hex>",
      "ledger_reference": null,
      "created": true
    },
    "audit_log": {
      "command_id": "AUDIT_<uuid>",
      "prev_hash": "<previous_hash>",
      "current_hash": "<sha256_hex>",
      "timestamp": "2026-09-30 11:30:00"
    }
  }
}
```

重複撤銷：HTTP 200、status=Success，data 僅含 ledger_event 與 mqtt_published，沒有新 audit_log；已取消舊文件的 Warning 回應。一般角色更新 data 僅有 mqtt_published；設定 Guest / 新撤銷另有 ledger_event 與 audit_log。

## API → Gateway / ESP32 封包規範

Topic：`home/security/gateway_{family_id}/auth_sync`，QoS 1，透過 mqtt_tls 使用 TLS / port 設定。
```json
{
  "event": "MEMBER_ROLE_CHANGED",
  "user_data": {
    "user_id": "guest_example",
    "role": "Revoked",
    "start_time": "2030-01-01 15:00:00",
    "end_time": "2026-09-30 19:30:00",
    "max_uses": 3
  }
}
```

撤銷與重送都使用實際存下的 end_time；不是傳入的 null。無 Gateway 套用 ACK，不直接操作 ESP32。

## Error Responses

| HTTP 狀態 | 錯誤碼 / 情境 | 說明 |
| --- | --- | --- |
| 400 | JSON / 缺核心欄位 / family_id / 角色 / 自我降權 | status=Error、msg |
| 403 | 非 Admin / 目標帳號全域停用 | 拒絕操作 |
| 404 | 家庭、目標 users 或可撤銷關係不存在 | 需先有合法平台資料 |
| 409 | 撤銷唯一 Admin | 先移轉管理權 |
| 405 | 非 POST | REQUEST_METHOD 必須 POST |
| 500 | DB / 其他內部錯誤 | 包括未被獨立處理的時間轉換錯誤 |

## 注意事項

- 原欄位保留，新增 reason_code / reason 與 data metadata。
- 此入口的 Guest 時間 / 次數驗證與 generate_guest_qr.py 不等同，不能宣稱所有 Guest 授權都被同一套期限驗證限制。
- Admin 自我降權保護適用任何非 Admin 角色；唯一 Admin 的數量檢查目前只在 Revoked 分支。
- 設定 Guest 每次新建授權事件；重送相同 Guest 變更不具冪等。
- mqtt_published 只代表本次發布到 Broker 的結果，實際 Gateway 套用需確認；此函式的 publish.single 可能等待網路操作，不是背景工作。
- 所有範例中的 `<uuid>`、`<sha256_hex>`、`<previous_hash>` 等為示意值；實際值由程式產生。Ledger Event 的 `PENDING` 只代表待上鏈紀錄已建立，不代表 IOTA 已確認。

## App 呼叫範例

正式部署請使用 HTTPS；以下為本機 CGI 路由示例，實際路由需與伺服器設定一致。

```bash
curl -X POST "http://localhost:8000/cgi-bin/update_member_role.py" \
  -H "Content-Type: application/json" \
  -d '{"payload":{"family_id":12,"admin_uid":"admin001","target_uid":"guest_example","target_role":"Revoked","reason_code":"ADMIN_MANUAL_REVOCATION","reason":"訪客離開"}}'
```
