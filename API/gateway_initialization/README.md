# 閘道器初始化與屋主綁定 API 與 Gateway 互動規範

## 基本資訊

| 項目 | 內容 |
| --- | --- |
| 對應 UC | UC1.3 閘道器初始化與屋主綁定 |
| 對應檔案 | `IOTA_API/API/gateway_initialization/provision_gateway_identity.py`、`gateway_initialize.py`、`get_gateway_initialization_status.py`、`gateway_init_common.py` |
| 方法 | 初始化及查詢以 POST / CGI stdin 部署，兩支程式未自行強制拒絕所有非 POST；provision 為 Gateway CLI；common 為匯入模組 |
| Endpoint | CGI 部署時可對應 `/cgi-bin/gateway_initialize.py`、`/cgi-bin/get_gateway_initialization_status.py` |
| Content-Type | application/json; charset=utf-8 |
| 是否使用 ECDH | 否。UC1.3 使用 `SECP256R1 / P-256` 建立 Gateway 長期 Identity Key；Gateway ↔ ESP32 ECDH 屬於 UC2.1。 |
| App 互動 | 屋主輸入帳號、密碼、場域名稱、Gateway 名稱與一次性初始化碼，發起首次 Gateway 綁定。 |
| Gateway 互動 | Gateway 本機先產生 P-256 Identity、公鑰 Fingerprint、Gateway ID 與一次性 `GWINIT_` 初始化碼；API 從本機 runtime 讀取 Identity，不接受 App 自行指定公鑰。 |
| 區塊鏈互動 | 初始化成功後建立 `SITE_GENESIS_CREATED` 待上鏈事件；目前寫入 `ledger_events` 並標記 `PENDING`，不虛構 IOTA 已確認。 |

## Gateway 本地 Provision

首次部署 Raspberry Pi Gateway 時先執行：

```bash
python -u gateway_initialization/provision_gateway_identity.py
```

程式會在 Gateway 本機建立：

```text
gateway_runtime/
├─ gateway_private_key.pem
├─ gateway_identity.json
└─ gateway_bootstrap.json
```

成功輸出範例：

```json
{
  "status": "Success",
  "msg": "UC1.3 Gateway identity provision 完成；initialization_token 僅顯示本次",
  "data": {
    "gateway_id": "GW_0A1B2C3D4E5F67890123",
    "curve": "SECP256R1",
    "public_key_fingerprint": "sha256_hex",
    "hardware_model": "RASPBERRY_PI",
    "firmware_version": "1.0.0",
    "initialization_token": "GWINIT_xxxxxxxxxxxxxxxxx",
    "expires_at": "2026-08-10T11:00:00Z"
  }
}
```

若已 provision 但遺失初始化碼，可保留原 Gateway Identity 並重新產生 Token：

```bash
python -u gateway_initialization/provision_gateway_identity.py --rotate-token
```

## POST Request Parameters

### `gateway_initialize.py`

| 欄位 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| user_id | string | true | 首位屋主的 user_id。 |
| password | string | true | 屋主密碼；目前 UC1.2 Access Token 尚未正式接入，因此使用 Bcrypt 驗證。 |
| family_name | string | true | 要建立的家庭 / 場域名稱，最長 100 字元。 |
| gateway_name | string | true | Gateway 顯示名稱，最長 100 字元。 |
| initialization_token | string | true | Gateway 本機 provision 產生的一次性 `GWINIT_` Token。 |

### `get_gateway_initialization_status.py`

| 欄位 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| user_id | string | true | 查詢者 user_id。 |
| password | string | true | 查詢者密碼。 |
| gateway_id | string | false | 可省略；省略時使用本機 `gateway_identity.json` 的 Gateway ID。只允許查詢目前實體 Gateway。 |

## App / Gateway → API Request Packet

### Gateway 初始化

```json
{
  "payload": {
    "user_id": "uc13_admin",
    "password": "Pass12345",
    "family_name": "台北住家",
    "gateway_name": "台北住家 Gateway",
    "initialization_token": "GWINIT_xxxxxxxxxxxxxxxxx"
  }
}
```

### 初始化狀態查詢

```json
{
  "payload": {
    "user_id": "uc13_admin",
    "password": "Pass12345",
    "gateway_id": "GW_0A1B2C3D4E5F67890123"
  }
}
```

## Processing Rules

| 編號 | 規則 |
| --- | --- |
| 1 | Gateway 第一次部署時，以 `SECP256R1 / P-256` 產生長期 Private/Public Key。 |
| 2 | 由 Public Key DER 的 SHA-256 Fingerprint 推導 `gateway_id`，並保存完整 Fingerprint。 |
| 3 | Gateway Private Key 只保存在本機，不寫入 MySQL、Audit Log 或 Ledger Payload。 |
| 4 | Provision 產生一次性 `GWINIT_` Token；本機只保存其 SHA-256 Hash。 |
| 5 | API 讀取 POST JSON payload，檢查 `user_id`、`password`、`family_name`、`gateway_name`、`initialization_token`。 |
| 6 | 以 Bcrypt 驗證使用者密碼，並要求 `users.status = Active`。 |
| 7 | API 從本機 `gateway_identity.json` 讀取 Gateway Identity，不接受 App 自行指定 Public Key。 |
| 8 | 驗證 Public Key 格式、P-256 Curve、Fingerprint、Gateway ID，以及 Private/Public Key Pair 是否一致。 |
| 9 | 驗證 Initialization Token 是否正確、未過期、未被使用。 |
| 10 | 驗證同一 Gateway ID 或 Public Key Fingerprint 未被其他屋主 / Gateway 佔用。 |
| 11 | 在同一個 MySQL Transaction 中建立或補齊 `families`、`user_families`、`gateways`、`audit_logs`、`ledger_events`。 |
| 12 | 寫入 `audit_logs`，`action=GATEWAY_INITIALIZED`。 |
| 13 | 建立 `SITE_GENESIS_CREATED` 事件，寫入 `ledger_events`，初始 `status=PENDING`。 |
| 14 | DB Commit 成功後，Gateway 本機 Bootstrap 才標記 `consumed=true` 並清除 Token Hash。 |
| 15 | 若同一 Gateway 已由相同 Owner 正式初始化，重送請求採 Idempotent 回應，不建立第二個 Family 或 Genesis。 |
| 16 | `get_gateway_initialization_status.py` 只允許查詢本機 Gateway；已初始化時查詢者須為該場域 Admin。 |

## API 對 SQL 寫入內容

| 資料表 | 寫入 / 更新欄位 | 觸發時機與說明 |
| --- | --- | --- |
| users | SELECT `id`, `user_id`, `username`, `password_hash`, `status` | 初始化與查詢時驗證使用者及 Bcrypt 密碼。 |
| families | INSERT `family_name`, `admin_uid` | Gateway 首次正式初始化且不存在既有相容場域時建立。 |
| user_families | INSERT / UPDATE `user_id`, `family_id`, `role=Admin` | 將首位屋主設定為該場域 Admin。 |
| gateways | INSERT / UPDATE `gateway_id`, `family_id`, `owner_user_id`, `gateway_name`, `status`, `public_key`, `public_key_fingerprint`, `hardware_model`, `firmware_version`, `binding_method`, `initialized_at` | Gateway 首次正式綁定，或補齊 UC1.4 舊測試資料。 |
| audit_logs | INSERT `GATEWAY_INITIALIZED` | 初始化成功後寫入稽核 Hash Chain。 |
| ledger_events | INSERT `event_id`, `dedup_key`, `uc_id=UC1.3`, `event_type=SITE_GENESIS_CREATED`, `family_id`, `gateway_id`, `created_by`, `payload`, `payload_hash`, `status=PENDING` | 建立場域 Genesis 待上鏈事件。 |

## API → App / Gateway Response Packet

### 初始化成功（HTTP 201）

```json
{
  "status": "Success",
  "msg": "UC1.3 Gateway 初始化、屋主綁定與 Genesis 待上鏈事件建立完成",
  "data": {
    "already_initialized": false,
    "family_id": 12,
    "family_name": "台北住家",
    "created_new_family": true,
    "owner": {
      "user_id": "uc13_admin",
      "role": "Admin"
    },
    "gateway": {
      "gateway_id": "GW_0A1B2C3D4E5F67890123",
      "gateway_name": "台北住家 Gateway",
      "status": "Active",
      "hardware_model": "RASPBERRY_PI",
      "firmware_version": "1.0.0",
      "curve": "SECP256R1",
      "public_key_fingerprint": "sha256_hex",
      "binding_method": "PHYSICAL_LOCAL_CONNECTION"
    },
    "audit_log": {
      "command_id": "tx-uuid",
      "action": "GATEWAY_INITIALIZED",
      "current_hash": "sha256_hex"
    },
    "genesis_event": {
      "event_id": "<uuid>",
      "dedup_key": "UC1.3:SITE_GENESIS_CREATED:FAMILY:12",
      "uc_id": "UC1.3",
      "event_type": "SITE_GENESIS_CREATED",
      "status": "PENDING",
      "payload_hash": "sha256_hex",
      "ledger_reference": null,
      "created": true,
      "note": "目前僅建立待上鏈事件；未接入 IOTA Ledger Worker 前維持 PENDING"
    }
  }
}
```

### 已初始化的 Idempotent Response（HTTP 200）

相同 owner 密碼驗證成功且 Gateway identity 與 DB 一致時回傳；此分支不重新驗證 token 的未消耗狀態，但仍要求初始化請求的必要欄位非空。


```json
{
  "status": "Success",
  "msg": "Gateway 已完成初始化",
  "data": {
    "already_initialized": true,
    "family_id": 12,
    "family_name": "台北住家",
    "gateway_id": "GW_<fingerprint_prefix>",
    "gateway_name": "台北住家 Gateway",
    "gateway_status": "Active",
    "owner_user_id": "uc13_admin",
    "public_key_fingerprint": "<sha256_hex>",
    "initialized_at": "2026-09-30 11:30:00",
    "genesis_event": {
      "event_id": "<uuid>",
      "event_type": "SITE_GENESIS_CREATED",
      "ledger_status": "PENDING",
      "payload_hash": "<sha256_hex>",
      "ledger_reference": null
    }
  }
}
```

### 狀態查詢成功

```json
{
  "status": "Success",
  "data": {
    "gateway_id": "GW_0A1B2C3D4E5F67890123",
    "initialized": true,
    "family_id": 12,
    "family_name": "台北住家",
    "owner_user_id": "uc13_admin",
    "gateway_name": "台北住家 Gateway",
    "gateway_status": "Active",
    "binding_method": "PHYSICAL_LOCAL_CONNECTION",
    "bootstrap": {
      "state": "CONSUMED"
    },
    "genesis_event": {
      "event_type": "SITE_GENESIS_CREATED",
      "ledger_status": "PENDING",
      "payload_hash": "sha256_hex",
      "ledger_reference": null
    }
  }
}
```


## Error Responses

本組 API 實際不輸出舊表中的 AUTH_FAILED 等具名 code；以下以 HTTP 與 msg 情境說明。

| HTTP 狀態 | 錯誤碼 / 情境 | 說明 |
| --- | --- | --- |
| 400 | 無輸入 / JSON / 缺欄位 / 名稱超長 | 初始化必要欄位或格式不符 |
| 401 | 帳號或密碼錯誤 | Bcrypt 驗證失敗 |
| 403 | 帳號停用 / token 驗證失敗 / 查詢權限不足 | 依實際流程拒絕 |
| 409 | identity / fingerprint / key pair / owner / bootstrap 衝突 | 包含初始化碼已使用 |
| 410 | 初始化碼過期 | Gateway 端 rotate-token |
| 500 | DB / 已初始化缺 Genesis / 其他內部錯誤 | API 回 status=Error 與 msg，可能含 data 或 detail |

## 注意事項

- `gateway_private_key.pem` 只能存在 Raspberry Pi / Gateway 本機，不得傳給 App、Server 或寫入資料庫。
- `gateway_runtime/` 應加入 `.gitignore`，禁止 Private Key 或 Bootstrap 狀態被提交到 Git。
- Initialization Token 明文只在 provision 成功時顯示一次；本機只保存 SHA-256。
- `gateway_initialize.py` 不接受 App 自行提供 `gateway_id`、Public Key 或 Fingerprint，這些資料由 Gateway 本機 Identity 決定。
- UC1.3 的 P-256 Identity Key 是 Gateway 長期身分，不等於 UC2.1 Gateway ↔ ESP32 的 ECDH Session Key。
- `SITE_GENESIS_CREATED` 目前只建立正式待上鏈封包，`ledger_events.status=PENDING`；真正 IOTA 提交應由後續 Ledger Worker 處理。
- 初始化資料、Audit Log 與 `ledger_events` 必須在同一個 MySQL Transaction 中完成。
- 同一 Family 的 Genesis 透過 `dedup_key` 防止重複建立。
- 目前 UC1.2 尚未正式提供 Access Token，因此 UC1.3 暫時使用 `user_id + password`；未來可改為 Bearer Token，但不影響 Gateway Identity 與初始化流程。

## App 呼叫範例

App 端正式部署時應透過 HTTPS POST；目前本機測試可直接使用 CGI stdin。

```http
POST /cgi-bin/gateway_initialize.py
Content-Type: application/json; charset=utf-8
```

```json
{
  "payload": {
    "user_id": "uc13_admin",
    "password": "Pass12345",
    "family_name": "台北住家",
    "gateway_name": "台北住家 Gateway",
    "initialization_token": "GWINIT_xxxxxxxxxxxxxxxxx"
  }
}
```

本機測試：

```bash
printf '{"payload":{"user_id":"uc13_admin","password":"Pass12345","family_name":"台北住家","gateway_name":"台北住家 Gateway","initialization_token":"GWINIT_xxxxxxxxxxxxxxxxx"}}' \
| python -u gateway_initialization/gateway_initialize.py
```

狀態查詢：

```bash
printf '{"payload":{"user_id":"uc13_admin","password":"Pass12345"}}' \
| python -u gateway_initialization/get_gateway_initialization_status.py
```

## gateway_init_common.py 共用函式 I/O

此檔由 provision_gateway_identity.py、gateway_initialize.py、get_gateway_initialization_status.py 匯入，沒有可單獨呼叫的 HTTP endpoint。原 App POST 欄位保留。

| 函式 | 輸入 | 輸出 / 副作用 |
| --- | --- | --- |
| normalize_payload(raw_data) | JSON 字串 | payload dict；接受包裝或直接 object；錯誤 ApiError |
| get_conn() | DB_HOST / DB_USER / DB_PASS / DB_NAME | DictCursor、autocommit=False 的 DB connection |
| load_and_validate_identity(state_dir=None) | 本機 identity / private key | identity dict；驗證 P-256、公鑰 fingerprint、Gateway ID、private/public 一致 |
| verify_initialization_token(initialization_token, identity, state_dir=None) | 初始化碼及 identity | bootstrap dict；驗證同 Gateway、未消耗、期限、Hash |
| require_active_user_password(cursor, user_id, password, for_update=False) | DB cursor 及密碼 | 含 id/user_id 的使用者 dict；要求 Active 與 Bcrypt 驗證 |
| append_audit_log(cursor, *, user, family_id, action, parameters, status="Verified", decision="ALLOW", reason=None) | user 必須含 id、user_id | INSERT audit；回 command_id/timestamp/prev_hash/current_hash；不 commit |
| build_genesis_payload(*, user_id, family_id, family_name, identity) | 已驗證身分、家庭與 identity | 只回 UC1.3 業務 Payload：owner_binding、gateway、genesis；不寫 DB |
| insert_genesis_ledger_event(cursor, *, user_id, family_id, identity, genesis_payload) | 上述 payload 與 cursor | 呼叫 common.enqueue_ledger_event；回統一 metadata，新增 UUID event_id、created=true，既有 created=false；不 commit |
| fetch_gateway_initialization(cursor, gateway_id, for_update=False) | Gateway ID | join gateways/families 的 dict 或 None |
| fetch_genesis_event(cursor, family_id) | 家庭 ID | 依 UC1.3 dedup_key 查 Ledger DB row 或 None |
| require_admin_access_to_gateway(cursor, user_id, gateway) | 使用者與 Gateway dict | Admin 時無回傳值，否則 ApiError 403 |
| mark_bootstrap_consumed(*, family_id, identity, state_dir=None) | commit 後的家庭與 identity | 更新本機 bootstrap 為 consumed，清除 token hash；不寫 DB |

## UC1.3 Ledger 共用化更動

insert_genesis_ledger_event 已委派 common/ledger_event_service.py，統一 envelope、canonical JSON、完整事件 payload_hash、UUID event_id 與 metadata。dedup_key 保留 `UC1.3:SITE_GENESIS_CREATED:FAMILY:{family_id}`。build_genesis_payload 只產生業務 payload；不可再自行包完整 envelope 傳入 genesis_payload。

初始化成功仍回 data.genesis_event，而不是改成 data.ledger_event；其中新增 dedup_key、uc_id、ledger_reference、created。首次成功 201，重送 200。DB commit 後寫本機 bootstrap 失敗時回成功並加 data.local_state_warning，重送相同 owner 請求可修復本機狀態。

共用模組的 append_audit_log 仍是 UC1.3 本地 writer，簽名與 common/audit_log_service.py 不相同，不要互換。它保留 user.id / u_id 和 tx- 前綴。

## 共用函式呼叫範例

在 gateway_initialize.py 已完成使用者及 identity 驗證並更新家庭 / Gateway 後：

```python
# cursor 已屬於呼叫端的 transaction，identity/user/family 已驗證。
genesis_payload = build_genesis_payload(
    user_id=user["user_id"], family_id=family_id,
    family_name=family_name, identity=identity,
)
event = insert_genesis_ledger_event(
    cursor, user_id=user["user_id"], family_id=family_id,
    identity=identity, genesis_payload=genesis_payload,
)
# 家庭、Gateway、audit、ledger 全部成功後由呼叫端 conn.commit()。
```

所有範例中的 `<uuid>`、`<sha256_hex>`、`<previous_hash>` 等為示意值；實際值由程式產生。Ledger Event 的 `PENDING` 只代表待上鏈紀錄已建立，不代表 IOTA 已確認。
