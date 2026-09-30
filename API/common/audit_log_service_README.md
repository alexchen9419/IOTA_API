# Audit Log 共用服務的 I/O 規範

新增文件，沿用既有 README 章節風格。這是 Python 共用模組，不是獨立 HTTP API。

## 基本資訊

| 項目 | 內容 |
| --- | --- |
| 實作檔案 | `IOTA_API/API/common/audit_log_service.py` |
| 方法 / Endpoint | Python import / append_audit_log；沒有 HTTP endpoint |
| 輸入 / 輸出 | DB cursor + 稽核欄位 → command_id / prev_hash / current_hash / timestamp |
| SQL | audit_logs 鏈式日誌 |
| 交易責任 | 不開 connection、不 commit、不 rollback；呼叫端負責 |
| Ledger / MQTT | 不建立 ledger_events，不發 MQTT、不直接上鏈 |

## Function Request Parameters

| 欄位 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| cursor | DB cursor | true | 呼叫端 DictCursor，同一 transaction |
| actor_id | string / None | true（可 None） | 操作者 ID；此值寫本地 audit，模組不自動雜湊 |
| actor_type | string | true | 例如 USER / GATEWAY |
| family_id / device_id | integer or None / string or None | true（可 None） | 稽核範圍，需明確傳參數 |
| action | string | true | 稽核動作名稱；不自動轉大寫或限長 |
| parameters | dict | true | 業務稽核參數，會 JSON 化並參與 hash |
| status | string | false | 預設 Verified |
| decision | string | false | 預設 ALLOW；服務不自行做政策判定 |
| reason | string / None | false | 預設 None |
| raw_data | dict / None | false | 省略時用 parameters；會寫 DB 支援的 raw_data 欄位，但不在 current_hash 的 hash_material 中 |

## Python → 共用服務 Request Packet

```python
from common.audit_log_service import append_audit_log

with conn.cursor() as cursor:
    audit = append_audit_log(
        cursor, actor_id="admin001", actor_type="USER",
        family_id=12, device_id=None,
        action="UC3.4_GUEST_TOKEN_ISSUED",
        parameters={"authorization_id": grant_id},
        status="Verified", decision="ALLOW",
        reason="GUEST_QR_AUTHORIZATION_ISSUED",
    )
    # 同 cursor 寫業務及 ledger；全部成功後才 conn.commit()。
```

## Processing Rules

| 編號 | 規則 |
| --- | --- |
| 1 | SHOW COLUMNS audit_logs 取得支援欄位與 timestamp 型別。 |
| 2 | previous hash 優先使用 current_hash，否則 hash；依 id、timestamp 或 hash 欄位排序查最新非空 hash。 |
| 3 | 無前一筆或不支援 hash 欄位時 prev_hash 為 64 個 0。 |
| 4 | 產生 AUDIT_<uuid> command_id；timestamp 為 UTC datetime，若資料表欄位是數值型別則用 epoch 秒。 |
| 5 | hash_material 包含 command_id、actor_id/type、family_id、device_id、action、parameters、status、decision、reason、str(timestamp)、prev_hash。 |
| 6 | 以排序、緊湊 JSON（default=str）計算 SHA-256 current_hash；raw_data 本身不在 hash_material。 |
| 7 | 動態 INSERT schema 已有欄位；current_hash 也對應舊版 hash 欄位；parameters/raw_data 寫 JSON 字串。 |
| 8 | 不查角色、不阻擋未授權操作、不去重；每次呼叫會寫新稽核。 |

## API 對 SQL 寫入內容

| 資料表 | 寫入 / 更新欄位 | 觸發時機與說明 |
| --- | --- | --- |
| audit_logs | SELECT latest previous hash / INSERT command_id, user_id, actor_id, actor_type, device_id, family_id, action, parameters, raw_data, status, decision, reason, prev_hash, current_hash, hash, timestamp, created_at | 只插入表中存在的支援欄位；user_id/actor_id 都由 actor_id 對應 |
| users / user_families | 不查詢 | 身份及角色由呼叫端驗證 |
| ledger_events | 不寫入 | 需呼叫 ledger_event_service.py |

## 共用服務 → 呼叫端 Response Packet

```json
{
  "command_id": "AUDIT_<uuid>",
  "prev_hash": "<previous_hash>",
  "current_hash": "<sha256_hex>",
  "timestamp": "2026-09-30 11:30:00"
}
```

回傳沒有 status=Success、HTTP code、written 或 action；timestamp 依實際欄位型別是 datetime / epoch 整數，HTTP 包裝者需自行 JSON 序列化。

## API → Gateway / ESP32 封包規範

不傳封包，也不接收 ACK。audit_log metadata 由各 API 放入 data.audit_log 等欄位。

## Error Responses

無 HTTP 回應或 ApiError 封裝。audit_logs 缺表、SHOW COLUMNS / SELECT / INSERT、欄位約束等例外直接向上傳遞；呼叫端 rollback，不能在稽核失敗時自行宣稱所有資料都成功提交。

## 注意事項

- 此 writer 與 device_pair.py、decommission_device.py、gateway_init_common.py 的本地同名 writer 簽名不同；參數與回應格式不能直接互換。
- 模組不自動移除敏感資料；parameters / raw_data / reason 請由呼叫端限制，不傳明文憑證。
- Hash chain 是本地稽核資料，不代表已提交 IOTA。
- 此模組沒有鎖定或序列化所有 audit 寫入，併發交易可能讀到相同 prev_hash；不能宣稱全域鏈式稽核已保證無分叉。
- 不驗證既有鏈、不檢查 metadata 完整性，也沒有自動重試或去重。
- 共用 module 不擁有交易，與 Ledger 和業務資料使用相同 conn / cursor。
