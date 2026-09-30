# 設備安全配對 API 與 ESP32 互動規範

以原 `device_pair/README.md` 為基礎，更新 Admin 與 Gateway 檢查、device_credentials 及實際回應。

## 基本資訊

| 項目 | 內容 |
| --- | --- |
| 對應檔案 | `IOTA_API/API/device_pair/device_pair.py` |
| 對應 UC | UC2.1 |
| 方法 | POST / CGI stdin；程式未自行檢查 REQUEST_METHOD |
| Endpoint | `/cgi-bin/device_pair.py`（沿用原文件部署映射） |
| Content-Type | application/json; charset=utf-8 |
| 是否使用 ECDH | secp256r1 / P-256，HKDF 派生 session key |
| App 互動 | 場域 Admin 發起配對 |
| ESP 互動 | 輸入可帶設備公鑰；目前回應不含 gateway_public_key_pem，尚非完整 ESP 握手傳輸 |
| Ledger Event | DEVICE_REGISTERED_AND_PAIRED |

## POST Request Parameters

| 欄位 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| owner_user_id | string | true（或 user_id） | 必須是 Active users 且目標家庭 Admin |
| device_id | string | true | 設備 ID |
| family_id | integer | false | 省略從已初始化 Gateway 解析；提供時必須一致 |
| gateway_id | string | false | 預設 GW_001；必須存在並有 family_id，狀態 Active / Initialized |
| device_name | string | false | 預設 未命名裝置 |
| device_type | string | false | 程式預設 smart_lock；原文件列必填，本版按實際行為修正 |
| device_public_key_pem | string | false | 省略會產生模擬 ESP key；正式串接應傳有效 P-256 public PEM |
| physical_state | string | false | 只用於 Ledger initial_status，預設 UNKNOWN；此程式未寫 devices.physical_state |

## App / ESP32 → API Request Packet

此檔需要 `{ "payload": { ... } }` 包裝。以下省略 device_public_key_pem，會走模擬設備分支；正式串接需加入真正 PEM。
```json
{
  "payload": {
    "owner_user_id": "admin001",
    "family_id": 12,
    "gateway_id": "GW_001",
    "device_id": "ESP32_LOCK_001",
    "device_name": "客廳門鎖",
    "device_type": "smart_lock"
  }
}
```

## Processing Rules

| 編號 | 規則 |
| --- | --- |
| 1 | 讀 payload，檢查 owner_user_id（可用 user_id）、device_id，套用其他預設值。 |
| 2 | Gateway 在本程序產生配對用 ECDH key；與設備公鑰計算 shared secret，HKDF 派生 session key，只保留 hash。 |
| 3 | 檢查 Gateway 已初始化、狀態可配對，家庭與請求一致。 |
| 4 | 查 users.id/status 並驗證家庭 Admin；帳號查不到不再放行。 |
| 5 | 已綁另一家庭或已除役設備回 409。未除役的既有設備可更新配對資料。 |
| 6 | Upsert devices、device_credentials，credential_id 固定由 device_id 的 SHA-256 前 32 位組成。 |
| 7 | 寫本檔既有 audit_log 與 UC2.1 Ledger Event，commit 後才回成功；未提交資料在連線關閉時回滾。 |
| 8 | 每次 ECDH 使用新 key，session hash 可能改變；dedup_key 含 session hash，不是單靠 device_id 防止重配對。 |

## API 對 SQL 寫入內容

| 資料表 | 寫入 / 更新欄位 | 觸發時機與說明 |
| --- | --- | --- |
| gateways | SELECT family_id, status | 不存在或未初始化不能配對 |
| users / user_families | SELECT id, status / role | 要求帳號 Active 且家庭 Admin |
| devices | INSERT / UPDATE 設備與家庭、Gateway、owner、公鑰、session_key_hash、paired_at；status=Active、pairing_status=paired；清空 revoked_at/by/reason | 不持久化明文 session key 或配對私鑰 |
| device_credentials | INSERT / UPDATE CRED_ ID, public_key_hash, status=Active, issued_at；清空撤銷資訊 | 供 UC2.3 之後撤銷 |
| audit_logs | INSERT DEVICE_REGISTERED | 本檔內部 append_audit_log，回應名稱由原 ledger 改為 audit_log |
| ledger_events | INSERT UC2.1 DEVICE_REGISTERED_AND_PAIRED / PENDING | dedup_key=UC2.1:DEVICE_REGISTERED_AND_PAIRED:FAMILY:{family_id}:DEVICE:{device_id}:SESSION:{session_key_hash} |

## API → App / ESP32 Response Packet

HTTP 200；下列是省略設備公鑰的模擬分支。真實公鑰分支 simulated_device=false，device_side_verified 未執行設備端驗證時為 null。
```json
{
  "status": "Success",
  "msg": "裝置註冊與安全配對成功，Ledger Event 已加入待上鏈佇列",
  "data": {
    "device_id": "ESP32_LOCK_001",
    "device_name": "客廳門鎖",
    "device_type": "smart_lock",
    "gateway_id": "GW_001",
    "owner_user_id": "admin001",
    "pairing_status": "paired",
    "ecdh_curve": "secp256r1",
    "simulated_device": true,
    "device_side_verified": true,
    "device_public_key_hash": "<sha256_hex>",
    "gateway_public_key_hash": "<sha256_hex>",
    "session_key_hash": "<sha256_hex>",
    "credential_id": "CRED_<device_hash_prefix>",
    "audit_log": {
      "command_id": "tx-<uuid>",
      "prev_hash": "<previous_hash>",
      "current_hash": "<sha256_hex>"
    },
    "ledger_event": {
      "event_id": "<uuid>",
      "dedup_key": "UC2.1:DEVICE_REGISTERED_AND_PAIRED:FAMILY:12:DEVICE:ESP32_LOCK_001:SESSION:<hash>",
      "uc_id": "UC2.1",
      "event_type": "DEVICE_REGISTERED_AND_PAIRED",
      "status": "PENDING",
      "payload_hash": "<sha256_hex>",
      "ledger_reference": null,
      "created": true
    }
  }
}
```

## API → ESP32 封包規範

此檔未直接呼叫 MQTT 或等待設備 ACK。雖然 DB 保存 gateway_public_key，HTTP 回應只給 hash；要讓實體 ESP32 算出相同 session key，仍需建立 Gateway 公鑰傳輸及設備確認流程。不能以 simulated_device=true 的結果認定實體握手成功。

## Error Responses

此檔使用 `{status, msg}`，部分情境含 data / detail，沒有統一 code。

| HTTP 狀態 | 錯誤碼 / 情境 | 說明 |
| --- | --- | --- |
| 400 | JSON / 缺欄位 / ValueError | 包括 Gateway 不存在、狀態不可配對、家庭不一致、公鑰解析等 ValueError |
| 403 | PermissionError | 帳號不存在、非 Active 或不是家庭 Admin |
| 409 | 設備衝突 | 已有其他家庭綁定或設備已除役 |
| 500 | 其他例外 | SQL / 其他公鑰例外；可能帶 detail |

## 注意事項

- 沿用 payload 包裝，原 data.ledger 已改為 data.audit_log，另新增 credential_id 與 ledger_event。
- 正式 ESP32 應使用自己的 public PEM；傳入測試字串 "-----BEGIN PUBLIC KEY-----..." 不是有效 PEM。
- 此檔的角色驗證未與 Bearer Token 身分綁定。
- 所有範例中的 `<uuid>`、`<sha256_hex>`、`<previous_hash>` 等為示意值；實際值由程式產生。Ledger Event 的 `PENDING` 只代表待上鏈紀錄已建立，不代表 IOTA 已確認。

## App 呼叫範例

正式部署請使用 HTTPS；以下為本機 CGI 路由示例，實際路由需與伺服器設定一致。

```bash
curl -X POST "http://localhost:8000/cgi-bin/device_pair.py" \
  -H "Content-Type: application/json" \
  -d '{"payload":{"owner_user_id":"admin001","family_id":12,"gateway_id":"GW_001","device_id":"ESP32_LOCK_001","device_name":"客廳門鎖","device_type":"smart_lock"}}'
```
