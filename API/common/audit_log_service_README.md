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
| raw_data | dict / None | false | 省略時用 parameters；會寫 DB 支援的 raw_data 欄位；預設不在 hash_material 中 |
| command_id | string / None | false | 保留各端點自己的 id 格式（如 `tx-…`、`MTN_…`、控制指令 id）；省略時 `AUDIT_<uuid>` |
| user_id | string / None | false | 寫入 `user_id` 欄位；省略時同 actor_id（可明確傳 None） |
| u_id | integer / None | false | users.id；有給時寫入 `u_id` 並加入 hash_material |
| hash_raw_data | bool | false | 預設 False；True 時 raw_data 也加入 hash_material（控制指令、裝置回報、儀表板用） |

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
| 2 | 若有 `audit_chain_lock` 表，先 `SELECT … WHERE id=1 FOR UPDATE` 鎖住鏈（直到呼叫端 commit / rollback）；再以 `SELECT … ORDER BY id DESC LIMIT 1 FOR UPDATE` 讀最新非空 current_hash（無則 hash）。 |
| 3 | 無前一筆或不支援 hash 欄位時 prev_hash 為 64 個 0。 |
| 4 | 產生 AUDIT_<uuid> command_id；timestamp 為 UTC datetime，若資料表欄位是數值型別則用 epoch 秒。 |
| 5 | hash_material 包含 command_id、actor_id/type、family_id、device_id、action、parameters、status、decision、reason、str(timestamp)、prev_hash；有給 u_id 才加 u_id，hash_raw_data=True 才加 raw_data（不傳這兩項時與舊版公式完全相同）。 |
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

- 2026-10-28 起所有寫 audit_logs 的端點（control_device、device_status_update、mqtt_topic_bridge、dashboard、decommission、device_pair、gateway_common、gateway_init_common、maintenance_mode、ota_update）都改呼叫本模組；各檔保留的同名 wrapper 只負責自己的 command_id 格式與回應格式，參數簽名彼此不同。
- 模組不自動移除敏感資料；parameters / raw_data / reason 請由呼叫端限制，不傳明文憑證。
- Hash chain 是本地稽核資料，不代表已提交 IOTA。
- 併發：`audit_chain_lock` 列鎖把所有 append 序列化到 commit 為止，避免兩個交易讀到同一 prev_hash 而分叉。舊 DB 沒有這張表時只剩鏈頭的 FOR UPDATE，在 REPEATABLE READ 下仍不會分叉，但並發時可能 deadlock 讓其中一個請求失敗；請跑 `API/migrations/2026-10-28_audit_chain_lock.sql`。
- 呼叫端必須 autocommit=False 並在同一 transaction 內完成業務寫入後 commit，鎖才有意義；鎖持有到 commit，append 之後不要做長時間 I/O。
- `verify_chain_links(rows)` 只檢查 prev_hash 銜接（舊公式與新公式的列可混合驗證）；歷史列由各檔舊公式產生且沒有公式版本欄位，無法逐列重算內容 hash。單元測試：`python -I tools/audit_chain_test.py`。
- 共用 module 不擁有交易，與 Ledger 和業務資料使用相同 conn / cursor。
