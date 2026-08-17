# 跨場域閘道器協作設定 API 與 Gateway Trust 互動規範

## 基本資訊

| 項目 | 內容 |
| --- | --- |
| 對應 UC | UC1.4 跨場域閘道器協作設定 |
| 對應檔案 | `api/gateway_collaboration/create_gateway_trust.py`、`confirm_gateway_trust.py`、`list_gateway_trusts.py`、`revoke_gateway_trust.py`、`gateway_common.py` |
| 方法 | POST |
| Endpoint | CGI 部署時可對應 `/cgi-bin/create_gateway_trust.py`、`/cgi-bin/confirm_gateway_trust.py`、`/cgi-bin/list_gateway_trusts.py`、`/cgi-bin/revoke_gateway_trust.py` |
| Content-Type | application/json; charset=utf-8 |
| 是否使用 ECDH | 否。UC1.4 建立的是 Gateway 間的授權信任關係；實際跨網路通訊與 NAT 屬於 UC5.6。 |
| App 互動 | Admin App 發起、確認、查詢或撤銷跨場域 Gateway 信任。 |
| Gateway 互動 | UC1.4 使用 UC1.3 已註冊的 `gateways` 資料建立 Trust Link；目前不直接讓兩台 Gateway 交換網路封包。 |

## POST Request Parameters

### `create_gateway_trust.py`

| 欄位 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| user_id | string | true | 發起信任綁定的 Admin user_id。 |
| source_gateway_id | string | true | 發起端 Gateway ID。 |
| target_gateway_id | string | true | 目標 Gateway ID。 |

### `confirm_gateway_trust.py`

| 欄位 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| user_id | string | true | 確認信任的 Admin user_id。 |
| link_id | string | true | `create_gateway_trust.py` 建立的信任關係 ID。 |
| pairing_token | string | true | 一次性短效 `GTPAIR_` Token。 |

### `list_gateway_trusts.py`

| 欄位 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| user_id | string | true | 查詢者 user_id。 |
| gateway_id | string | false | 只查指定 Gateway 相關信任。 |
| family_id | integer | false | 只查指定 Family 相關信任。 |
| status | string | false | `PENDING`、`ACTIVE`、`REVOKED`、`EXPIRED`。 |

### `revoke_gateway_trust.py`

| 欄位 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| user_id | string | true | 執行撤銷的 Admin user_id。 |
| link_id | string | true | 要撤銷的信任關係 ID。 |
| reason | string | false | 撤銷原因，最長 255 字元。 |

## App → API Request Packet

### 建立信任請求

```json
{
  "payload": {
    "user_id": "admin_001",
    "source_gateway_id": "GW_001",
    "target_gateway_id": "GW_002"
  }
}
```

### 確認信任

```json
{
  "payload": {
    "user_id": "admin_001",
    "link_id": "GTL_xxxxxxxxx",
    "pairing_token": "GTPAIR_xxxxxxxxx"
  }
}
```

### 查詢信任

```json
{
  "payload": {
    "user_id": "admin_001",
    "gateway_id": "GW_001",
    "status": "ACTIVE"
  }
}
```

### 撤銷信任

```json
{
  "payload": {
    "user_id": "admin_001",
    "link_id": "GTL_xxxxxxxxx",
    "reason": "不再管理此場域"
  }
}
```

## Processing Rules

| 編號 | 規則 |
| --- | --- |
| 1 | 所有 API 讀取 POST JSON payload，並驗證必要欄位。 |
| 2 | `create`、`confirm`、`list`、`revoke` 都要求 `users.status = Active`。 |
| 3 | 建立信任前驗證兩台 Gateway 都存在、`status=Active`，且已完成 UC1.3（`initialized_at` 非 NULL、具有 Public Key Fingerprint、`binding_method=PHYSICAL_LOCAL_CONNECTION`）。 |
| 4 | 建立與確認信任時，操作者必須同時是兩台 Gateway 所屬場域的 `Admin`。 |
| 5 | 撤銷信任時，只要操作者是任一端場域的 `Admin` 即可立即撤銷。 |
| 6 | 同一台 Gateway 不可與自己建立信任，且兩台 Gateway 必須屬於不同 Family；同一場域內不建立 UC1.4 Trust Link。 |
| 7 | Gateway Pair 會固定排序為 `gateway_a_id`、`gateway_b_id`，避免 A→B 與 B→A 建立重複資料。 |
| 8 | 建立信任時產生一次性 `GTPAIR_` Token；API 明文只回傳一次，DB 只保存 SHA-256。 |
| 9 | 新建立的 Trust Link 狀態為 `PENDING`，並具有絕對過期時間。 |
| 10 | 已存在 `ACTIVE` 的 Gateway Pair 不可重複建立；已有 `PENDING` 時也不重新回傳原 Token。 |
| 11 | `confirm` 驗證 `link_id`、Token 格式、Token Hash、有效期限、Gateway 狀態與 Admin 權限。 |
| 12 | 確認成功後 `PENDING → ACTIVE`，並將 `pairing_token_hash` 清為 `NULL`。 |
| 13 | 查詢前會將已過期的 `PENDING` 自動更新為 `EXPIRED`，並清除 Token Hash。 |
| 14 | `list` 只回傳查詢者在相關 Family 中具有 Admin 權限的 Gateway Trust，避免洩漏其他場域拓樸。 |
| 15 | `revoke` 將任何非已撤銷 Trust 更新為 `REVOKED`，清除 Token Hash 並寫入 `revoked_at`。 |
| 16 | 跨場域 Audit Event 會在兩個 `family_id` 各寫一筆，讓兩個場域都能由 UC5.3 查到該事件。 |

## API 對 SQL 寫入內容

| 資料表 | 寫入 / 更新欄位 | 觸發時機與說明 |
| --- | --- | --- |
| gateways | SELECT `gateway_id`, `family_id`, `owner_user_id`, `gateway_name`, `status`, `public_key_fingerprint` | 確認 Gateway 存在、所屬場域與生命週期狀態。 |
| families | SELECT `id`, `family_name` | 取得兩端 Gateway 的場域資訊。 |
| users | SELECT `id`, `user_id`, `username`, `status` | 驗證操作者存在且為 Active。 |
| user_families | SELECT `user_id`, `family_id`, `role` | 驗證 Admin 權限；建立 / 確認需同時為兩端 Admin，撤銷只需任一端 Admin。 |
| gateway_trust_links | INSERT / UPDATE `link_id`, `gateway_a_id`, `gateway_b_id`, `created_by`, `status`, `pairing_token_hash`, `expires_at`, `confirmed_at`, `revoked_at`, `updated_at` | 建立、確認、過期或撤銷 Gateway Trust。 |
| audit_logs | INSERT `GATEWAY_TRUST_REQUESTED` / `GATEWAY_TRUST_ESTABLISHED` / `GATEWAY_TRUST_REVOKED` | 跨場域事件在兩個 Family 各寫一筆 Audit Log。 |

## API → App Response Packet

### 建立 PENDING 信任

```json
{
  "status": "Success",
  "msg": "UC1.4 Gateway 信任請求已建立；pairing_token 僅回傳本次，請交由另一端完成確認",
  "data": {
    "link_id": "GTL_xxxxxxxxx",
    "source_gateway_id": "GW_001",
    "target_gateway_id": "GW_002",
    "canonical_gateway_pair": [
      "GW_001",
      "GW_002"
    ],
    "trust_status": "PENDING",
    "pairing_token": "GTPAIR_xxxxxxxxx",
    "expires_at": "2026-08-10 11:00:00",
    "expires_in_seconds": 600,
    "audit": []
  }
}
```

### 確認信任成功

```json
{
  "status": "Success",
  "msg": "UC1.4 跨場域 Gateway 信任綁定完成",
  "data": {
    "link_id": "GTL_xxxxxxxxx",
    "gateway_a_id": "GW_001",
    "gateway_b_id": "GW_002",
    "trust_status": "ACTIVE",
    "pairing_token_consumed": true,
    "audit": []
  }
}
```

### 查詢信任成功

```json
{
  "status": "Success",
  "msg": "UC1.4 Gateway 信任關係查詢完成",
  "data": {
    "user_id": "admin_001",
    "count": 1,
    "filters": {
      "gateway_id": "GW_001",
      "family_id": null,
      "status": "ACTIVE"
    },
    "trust_links": [
      {
        "link_id": "GTL_xxxxxxxxx",
        "trust_status": "ACTIVE",
        "gateway_a_id": "GW_001",
        "gateway_a_family_id": 12,
        "gateway_b_id": "GW_002",
        "gateway_b_family_id": 13
      }
    ]
  }
}
```

### 撤銷信任成功

```json
{
  "status": "Success",
  "msg": "UC1.4 Gateway 信任關係已撤銷",
  "data": {
    "link_id": "GTL_xxxxxxxxx",
    "gateway_a_id": "GW_001",
    "gateway_b_id": "GW_002",
    "previous_status": "ACTIVE",
    "trust_status": "REVOKED",
    "reason": "不再管理此場域",
    "audit": []
  }
}
```

## Error Responses

| HTTP 狀態 | 錯誤碼 / 情境 | 說明 |
| --- | --- | --- |
| 400 | INVALID_JSON / MISSING_FIELD / INVALID_STATUS | JSON 格式錯誤、缺少必要欄位、兩個 Gateway ID 相同、`family_id` 非整數或 status 不合法。 |
| 403 | USER_DISABLED / ROLE_DENIED / TOKEN_INVALID | 使用者不是 Active、沒有足夠 Admin 權限，或 `pairing_token` Hash 驗證失敗。 |
| 404 | USER_NOT_FOUND / GATEWAY_NOT_FOUND / TRUST_NOT_FOUND | 查無使用者、Gateway、Trust Link。 |
| 409 | GATEWAY_NOT_ACTIVE / TRUST_CONFLICT / DUPLICATE_RECORD | Gateway 非 Active、已有 ACTIVE/PENDING 關係、信任狀態衝突或資料庫 UNIQUE/FK 約束失敗。 |
| 410 | TOKEN_EXPIRED | `pairing_token` 已過期，必須重新呼叫 `create_gateway_trust.py`。 |
| 500 | DATA_INCONSISTENT / INTERNAL_ERROR | Gateway 關聯資料不完整、SQL 或伺服器內部錯誤。 |

## 注意事項

- `pairing_token` 明文只由 `create_gateway_trust.py` 回傳一次，資料庫只保存 SHA-256。
- `confirm_gateway_trust.py` 成功後會清除 `pairing_token_hash`，避免 Token 重複使用。
- `gateway_trust_links` 對 `(gateway_a_id, gateway_b_id)` 設有唯一限制，A↔B 只能存在一組 Trust Record。
- `PENDING`、`ACTIVE`、`REVOKED`、`EXPIRED` 是 UC1.4 Trust Link 的主要生命週期狀態。
- 建立與確認要求操作者同時為兩個場域 Admin；撤銷只需任一端 Admin，以便其中一端遭入侵或不再使用時可立即切斷信任。
- 跨場域事件會在兩個 Family 各保存 Audit Log，而不是只記錄在發起端。
- UC1.4 不處理 NAT、VPN、DDNS、Port Forwarding 或真實 Gateway-to-Gateway 網路路由；此部分屬於 UC5.6。
- UC1.4 不直接與 ESP32 互動；ESP32 仍透過各自所屬 Gateway 管理。
- 整合版 UC1.4 不接受只有 `gateway_id/family_id` 的舊測試 Gateway 作為正式信任端點；必須先經 UC1.3 完成 Identity 與屋主綁定。
- 目前 UC1.4 沿用現有專案的 `user_id + user_families.role` 權限模式；待 UC1.2 Access Token 完成後可將身分入口替換為 Bearer Token。

## App 呼叫範例

正式部署時 App 應透過 HTTPS POST；本機可直接用 CGI stdin 測試。

### 1. 建立 Gateway Trust

```http
POST /cgi-bin/create_gateway_trust.py
Content-Type: application/json; charset=utf-8
```

```bash
printf '{"payload":{"user_id":"admin_001","source_gateway_id":"GW_001","target_gateway_id":"GW_002"}}' \
| python -u gateway_collaboration/create_gateway_trust.py
```

### 2. 確認 Gateway Trust

將上一步回傳的 `link_id` 與 `pairing_token` 帶入：

```bash
printf '{"payload":{"user_id":"admin_001","link_id":"GTL_xxxxxxxxx","pairing_token":"GTPAIR_xxxxxxxxx"}}' \
| python -u gateway_collaboration/confirm_gateway_trust.py
```

### 3. 查詢 Gateway Trust

```bash
printf '{"payload":{"user_id":"admin_001","status":"ACTIVE"}}' \
| python -u gateway_collaboration/list_gateway_trusts.py
```

### 4. 撤銷 Gateway Trust

```bash
printf '{"payload":{"user_id":"admin_001","link_id":"GTL_xxxxxxxxx","reason":"不再管理此場域"}}' \
| python -u gateway_collaboration/revoke_gateway_trust.py
```
