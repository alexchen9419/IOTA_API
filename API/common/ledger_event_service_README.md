# Ledger Event 共用服務的 I/O 規範

新增文件，沿用原 README 的基本資訊、參數表、Processing Rules、SQL、輸出、錯誤及注意事項結構；此檔是 Python 共用模組。

## 基本資訊

| 項目 | 內容 |
| --- | --- |
| 實作檔案 | `IOTA_API/API/common/ledger_event_service.py` |
| 方法 / Endpoint | Python import / 函式呼叫；沒有 HTTP endpoint |
| 主要函式 | enqueue_ledger_event(cursor, *, ...) |
| 輸入 / 輸出 | 呼叫端 DB cursor + 事件欄位 → metadata dict |
| 交易責任 | 不建立 connection、不 commit、不 rollback；呼叫端負責 |
| IOTA 互動 | 只 enqueue，不呼叫 SDK / RPC、不 claim 或 confirm |
| 初始狀態 | PENDING |

## Function Request Parameters

| 欄位 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| cursor | DB cursor | true | 呼叫端 DictCursor，與業務/audit 同一交易 |
| uc_id / event_type / dedup_key | string | true | UC、事件種類、業務冪等鍵；此服務不驗證 UC 白名單 |
| family_id / gateway_id / created_by | integer or None / string or None | true（可 None） | 函式必須傳參數，可為 None |
| source | string | true | 事件來源 |
| actor / payload | dict | true | actor 和業務 payload；不傳整個 envelope 當 payload |
| device_id | string / None | false | 非空才加入完整 envelope |
| timestamp | string / None | false | 省略用現在 UTC Z；非空字串不另驗證或轉時區 |
| event_id | string / None | false | 省略 UUID v4；UC4.5 可保留來源 ID |
| schema_version | string | false | 預設 1.0 |
| extra_top_level | dict / None | false | 額外頂層欄位；不能覆蓋保留欄位 |

## Python → 共用服務 Request Packet

在呼叫端 transaction 內：

```python
from common.ledger_event_service import enqueue_ledger_event, hash_identifier

with conn.cursor() as cursor:
    # 同 cursor 先完成業務更新及 audit。
    event = enqueue_ledger_event(
        cursor, uc_id="UC3.4", event_type="GUEST_TOKEN_ISSUED",
        dedup_key="UC3.4:GUEST_TOKEN_ISSUED:GRANT:" + grant_id,
        family_id=12, gateway_id="GW_001", created_by="admin001",
        source="SERVER", actor={
            "actor_type": "USER",
            "actor_id_hash": hash_identifier("admin001"),
            "actor_role": "ADMIN",
        }, payload={"authorization_id": grant_id},
    )
# 成功才 conn.commit()；錯誤由呼叫端 conn.rollback()。
```

## Processing Rules

| 編號 | 規則 |
| --- | --- |
| 1 | 依 dedup_key SELECT ledger_events FOR UPDATE；存在即回 metadata created=false，不更改既有內容或 status。 |
| 2 | 新增使用傳入 event_id 或 UUID v4，建立 schema_version/uc_id/event_id/event_type/family_id/gateway_id/[device_id]/source/timestamp/actor/payload envelope。 |
| 3 | extra_top_level 的保留欄位禁止覆蓋：schema_version、uc_id、event_id、event_type、family_id、gateway_id、device_id、source、timestamp、actor、payload。 |
| 4 | canonical_json 使用 UTF-8、ensure_ascii=False、sort_keys=True、緊湊分隔；日期使用 ISO，其他不支援物件會轉 str。 |
| 5 | 對完整 envelope JSON 計算 SHA-256 payload_hash；這不是只對業務 payload 算 hash。 |
| 6 | SHOW COLUMNS 後寫支援欄位，至少需要 payload 和 payload_hash；新增 status=PENDING、retry_count=0。 |
| 7 | 不會檢查家庭角色、設備歸屬、來源簽章或敏感資訊；由各 API 先驗證並限制輸入。 |
| 8 | 成功 return 仍未代表 commit；必須由呼叫端與業務資料一起提交。 |

## API 對 SQL 寫入內容

| 資料表 | 寫入 / 更新欄位 | 觸發時機與說明 |
| --- | --- | --- |
| ledger_events | SELECT by dedup_key / INSERT event_id, dedup_key, uc_id, event_type, family_id, gateway_id, device_id, created_by, payload, payload_hash, status, retry_count | 只寫 schema 中存在的支援欄位；唯一鍵仍需由 SQL schema 提供 |
| devices / gateways | resolve_gateway_id 的輔助 SELECT | 此輔助函式僅解析 ID，不是權限/歸屬驗證 |
| audit_logs / 業務表 | 本模組不寫 | 由呼叫端處理 |

## 共用服務 → 呼叫端 Response Packet

```json
{
  "event_id": "<uuid>",
  "dedup_key": "UC3.4:GUEST_TOKEN_ISSUED:GRANT:QR_<uuid>",
  "uc_id": "UC3.4",
  "event_type": "GUEST_TOKEN_ISSUED",
  "status": "PENDING",
  "payload_hash": "<sha256_hex>",
  "ledger_reference": null,
  "created": true
}
```

重複也使用同一組 metadata 欄位，created=false，status 可能是目前 PROCESSING / CONFIRMED 等狀態，不固定 PENDING。get_ledger_event_by_dedup 則直接回完整 DB row 或 None，沒有補 created 欄位；兩種回傳不要混用。

## 其他共用函式

| 函式 | 輸入 | 輸出 / 說明 |
| --- | --- | --- |
| canonical_json(value) | 可 JSON 化資料 | 排序、緊湊 JSON string |
| sha256_hex(value) | str / bytes | 無 sha256: 前綴 hex |
| hash_identifier(value) | 識別資料 | 空值 None / "" → None；其他回 sha256:hex |
| utc_now() / iso_utc(value=None) | 無或 datetime | UTC aware datetime / UTC Z 字串；naive 視 UTC |
| get_ledger_event_by_dedup(cursor, dedup_key, for_update=False) | cursor、鍵 | DB row dict 或 None；可加 FOR UPDATE |
| resolve_gateway_id(cursor, *, family_id, preferred_gateway_id=None, device_id=None) | 範圍資訊 | 優先直接回 preferred，再查設備、家庭；查詢失敗可能略過，無結果 None |

## API → Gateway / ESP32 封包規範

本模組不發 MQTT、不回 App、不控制 Gateway 或 ESP32；metadata 由各 API 包裝成自己的 HTTP 回應。

## Error Responses

無 HTTP 錯誤包裝。SQL、唯一鍵、型別轉換等例外向上傳遞；缺 payload/payload_hash 必要欄位時拋 RuntimeError。呼叫端負責 rollback、回 HTTP 或記 log。

## 注意事項

- enqueue 的 dedup_key 去重不等於整個 HTTP 端點冪等；每次若產生新 ID / key，仍會新增。
- 併發插入仍依賴 UNIQUE 約束；服務不自動捕捉所有唯一鍵競爭並轉成功。
- 不會遮蔽密碼、Token、IP；必須在傳入 payload 前做必要處理。
- 欄位 introspection 不能代替執行完整 schema，缺表或約束仍會失敗。
- 所有範例中的 `<uuid>`、`<sha256_hex>`、`<previous_hash>` 等為示意值；實際值由程式產生。Ledger Event 的 `PENDING` 只代表待上鏈紀錄已建立，不代表 IOTA 已確認。
