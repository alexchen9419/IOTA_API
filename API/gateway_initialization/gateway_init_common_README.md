# Gateway 初始化共用模組的 I/O 規範

以原 gateway_initialization/README.md 為基礎整理共用模組規範；完整 App / Gateway 初始化封包保留在 [README.md](README.md)。

## 基本資訊

| 項目 | 內容 |
| --- | --- |
| 實作檔案 | `IOTA_API/API/gateway_initialization/gateway_init_common.py` |
| 對應 UC | UC1.3 |
| 方法 / Endpoint | Python import / 函式呼叫；無獨立 HTTP endpoint |
| 呼叫者 | provision_gateway_identity.py、gateway_initialize.py、get_gateway_initialization_status.py |
| 外部輸入 | Gateway 本機 runtime 檔案、DB connection/cursor、呼叫端已解析的欄位 |
| 本次核心變更 | Genesis 建立委派 common/ledger_event_service.py，統一 envelope、hash 及 metadata |
| 交易責任 | 此模組的 SQL helper 不 commit / rollback；呼叫端主程式管理 DB transaction |

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

## 共用函式 → 呼叫端 Response Packet

insert_genesis_ledger_event 回傳值示例：

```json
{
  "event_id": "<uuid>",
  "dedup_key": "UC1.3:SITE_GENESIS_CREATED:FAMILY:12",
  "uc_id": "UC1.3",
  "event_type": "SITE_GENESIS_CREATED",
  "status": "PENDING",
  "payload_hash": "<sha256_hex>",
  "ledger_reference": null,
  "created": true
}
```

這是共用函式的 dict，不是本模組直接輸出的 HTTP 本文。gateway_initialize.py 首次成功使用 HTTP 201，放在 data.genesis_event；重送使用 HTTP 200。

## Processing Rules

| 編號 | 規則 |
| --- | --- |
| 1 | 身分及 Bootstrap helper 驗證 P-256 key pair、fingerprint、Gateway ID、token Hash / 到期 / consumed。 |
| 2 | require_active_user_password 以 Bcrypt 驗證 users 的 Active 帳號；查詢權限 helper 要求家庭 Admin。 |
| 3 | build_genesis_payload 回傳 owner_binding、gateway、genesis 業務 Payload，不寫 DB。 |
| 4 | insert_genesis_ledger_event 將 Payload 交給共用 Ledger 服務包成完整事件，使用固定家庭 dedup_key。 |
| 5 | 本地 append_audit_log 寫 UC1.3 audit，與 common/audit_log_service.py 的參數 / hash 格式不同。 |
| 6 | 主程式提交家庭、Gateway、audit、Ledger 後才呼叫 mark_bootstrap_consumed。 |

## API 對 SQL 寫入內容

| 資料表 | 寫入 / 更新欄位 | 說明 |
| --- | --- | --- |
| users | SELECT 身分、password_hash、status | require_active_user_password；可加 FOR UPDATE |
| gateways / families | SELECT 初始化狀態、場域資訊 | fetch_gateway_initialization；業務 INSERT/UPDATE 由主程式完成 |
| user_families | SELECT role | require_admin_access_to_gateway；建立 Admin 關係由主程式處理 |
| audit_logs | INSERT command_id, user_id, actor_type, u_id, device_id=NULL, family_id, action, parameters, status, decision, reason, timestamp, prev_hash, current_hash | 本地 UC1.3 writer |
| ledger_events | SELECT / INSERT SITE_GENESIS_CREATED | insert 委派共用 Ledger；fetch_genesis_event 按家庭 dedup_key 查詢 |
| gateway_bootstrap.json | 本機檔案更新 consumed, consumed_at, bound_family_id, initialization_token_hash=NULL | 不屬於 SQL；commit 後才更新 |

## Error Responses

預期錯誤拋 ApiError(message, status_code, data=None)，由主程式 handle_api_error 包成 status=Error、msg，必要時附 data；不是直接輸出 AUTH_FAILED 等 code。
JSON / 缺欄位 400、帳密 401、停用或 token / Admin 權限 403、identity 或 consumed 衝突 409、token 過期 410；SQL 或其他未預期例外由主程式處理 500。

## API → Gateway / ESP32 封包規範

此檔不下發 MQTT 或 ESP32 指令。Gateway identity / Bootstrap 使用本機檔案；UC2.1 配對與 UC1.4 Gateway 間信任不由此模組完成。

## 注意事項

- Private key 只保存在 Gateway runtime，不進 DB、App、audit 或 Ledger；Token 明文只在 provision 當次顯示。
- UC1_3_GATEWAY_STATE_DIR 控制狀態目錄；預設 API/gateway_runtime。
- UC1_3_INITIALIZATION_TOKEN_TTL_SECONDS 預設 600，實際限制在 60–86400 秒。
- DB commit 與本機寫檔不是跨系統原子交易。主程式在 commit 後寫檔失敗會回 local_state_warning；不能宣稱 DB 與檔案永遠同時成功。
- Genesis ID 已改 UUID，DB event metadata 增加 created、dedup_key、uc_id、ledger_reference；App 原初始化輸入保留。
