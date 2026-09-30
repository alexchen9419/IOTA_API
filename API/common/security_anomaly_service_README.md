# 安全異常紀錄共用服務的 I/O 規範

依 UC4.4 串接版 security_anomaly_service.py 撰寫，沿用既有共用模組 README 的參數表、處理、SQL、輸出與錯誤格式。

## 基本資訊

| 項目 | 內容 |
| --- | --- |
| 實作檔案 | `IOTA_API/API/common/security_anomaly_service.py` |
| 對應 UC | UC4.4 |
| 方法 / Endpoint | Python import / record_security_anomaly(conn, req)；沒有 HTTP endpoint |
| 呼叫端 | report_security_anomaly.py、alert_service.py |
| 輸入 / 輸出 | 專用 DB connection + req dict → 紀錄結果 dict |
| 交易責任 | 本函式成功 commit，失敗 rollback；connection 由呼叫端提供及關閉 |
| Ledger Event | SECURITY_ANOMALY_RECORDED |
| MQTT / ESP | 本模組不發 MQTT、不操作 ESP32 |

## Function Request Parameters

| 參數 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| conn | DB connection | true | DictCursor、autocommit=False，專用連線，不混入其他未提交業務 |
| req | dict | true | 已解析的異常 object，直接傳資料，不包 payload / event 外層 |

## req 欄位規範

| 欄位 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| family_id | integer | true | 家庭 ID，程式使用 int() 轉換 |
| anomaly_type | string | true | 白名單類型，strip 後轉大寫 |
| request_id | string | false（MQTT 紀錄必須） | HTTP / 函式省略時產生 REQ_<uuid>；重送應提供相同非空 ID |
| is_abnormal | boolean | false | 只明確拒絕 false，省略沿用異常入口行為；Ledger 固定 true |
| severity | string | false | 預設 HIGH；LOW / MEDIUM / HIGH / CRITICAL |
| gateway_id / device_id | string / null | false | 可省略；提供 device_id 時查設備，檢查非空的家庭 / Gateway 歸屬 |
| source / detected_by | string | false | 來源優先 source、detected_by、POLICY_ENGINE，轉大寫；MQTT 強制 source=GATEWAY |
| timestamp | string | false | ISO 8601；省略現在 UTC；有時區轉 UTC，無時區視 UTC |
| actor_id | string / null | false | 本地 audit 會保留 ID，Ledger actor_id_hash 做 SHA-256 |
| actor_type / actor_role | string | false | 預設 UNKNOWN_OR_USER / UNKNOWN，轉大寫 |
| detection | object | false | 判定資訊，見巢狀欄位表 |
| source_evidence | object | false | 原始證據或上游 hash，見巢狀欄位表 |
| system_action | object | false | 回報已採取措施，本函式不執行封鎖 |
| notification | object | false | 回報通知資訊，本函式不發通知 |

## 巢狀欄位規範

| 欄位 | 型別 | 預設 / 省略行為 | 說明 |
| --- | --- | --- | --- |
| detection.detected_by | string | source | 記錄偵測來源 |
| detection.policy_decision | string | DENY | Ledger 轉大寫；audit 只有 DENY 仍為 DENY，其他值寫 ALLOW，未設白名單驗證 |
| detection.reason_code | string | SECURITY_POLICY_TRIGGERED | security_events / Ledger 的原因碼；audit reason 省略時使用 anomaly_type |
| detection.attempt_count | integer | 1 | int(value or 1)，輸入 0 也使用 1 |
| detection.observation_window_seconds | integer | 0 | int(value or 0) |
| source_evidence.source_ip / source_ip_hash | string | null | hash 欄位非空時優先使用；否則對原 IP 字串 SHA-256 |
| source_evidence.request_payload / request_payload_hash | JSON value / string | null | hash 欄位優先；原物件使用 canonical JSON 後 SHA-256 |
| source_evidence.evidence_hash | string | 自動計算 | 省略時依 IP hash、request hash、device、type、request_id 計算；DB 去掉前綴，Ledger 保留 |
| system_action.request_blocked | boolean | true | 回報值；不能據此認定本服務已實際阻擋請求 |
| system_action.temporary_lockout | boolean | false | 回報值，不實施鎖定 |
| system_action.lockout_seconds | integer | 0 | int(value or 0) |
| notification.notification_dispatched | boolean | false | HTTP / 函式保留上游回報；MQTT 在紀錄時強制 false |
| notification.notified_roles | array[string] | ["ADMIN"] | 非空 list 才使用，元素轉大寫；MQTT 強制 ADMIN / MEMBER |

四個巢狀區塊若不是 dict，會當成空物件套用預設，不是全部拒絕。布林使用 bool() 轉換，請傳 JSON true / false，勿傳 "false" 字串。既有 hash 直接採用，沒有重新驗證上游 hash 格式或內容。

## 異常白名單

| anomaly_type | 用途 |
| --- | --- |
| ILLEGAL_CONTROL_ATTEMPT | 未授權控制 |
| DEVICE_OFFLINE | 設備離線 |
| BRUTE_FORCE_ATTEMPT | 暴力嘗試 |
| TOKEN_ABUSE | 令牌濫用 |
| REPLAY_ATTACK | 重放攻擊 |
| SIGNATURE_VERIFICATION_FAILED | 簽章驗證失敗的偵測結果 |
| UNAUTHORIZED_DEVICE | 未授權設備 |
| DEVICE_TAMPERING | 設備遭竄改 |

## Python → 共用服務 Request Packet

```json
{
  "family_id": 12,
  "request_id": "GW_001_ALERT_000123",
  "gateway_id": "GW_001",
  "device_id": "ESP32_LOCK_001",
  "anomaly_type": "ILLEGAL_CONTROL_ATTEMPT",
  "severity": "HIGH",
  "is_abnormal": true,
  "source": "GATEWAY",
  "timestamp": "2026-09-30T19:30:00+08:00",
  "detection": {
    "detected_by": "GATEWAY",
    "policy_decision": "DENY",
    "reason_code": "PERMISSION_DENIED",
    "attempt_count": 1,
    "observation_window_seconds": 60
  },
  "source_evidence": {
    "source_ip": "192.0.2.10",
    "request_payload": {
      "action": "UNLOCK"
    }
  },
  "system_action": {
    "request_blocked": true,
    "temporary_lockout": false,
    "lockout_seconds": 0
  },
  "notification": {
    "notification_dispatched": false,
    "notified_roles": [
      "ADMIN",
      "MEMBER"
    ]
  }
}
```

```python
from common.security_anomaly_service import record_security_anomaly

# conn 為呼叫端新建的專用 DictCursor、autocommit=False 連線。
try:
    result = record_security_anomaly(conn, req)
    # 已 commit；不可把 MQTT 發送失敗當成回滾這筆紀錄的理由。
finally:
    conn.close()
```

## Processing Rules

| 編號 | 規則 |
| --- | --- |
| 1 | 驗證家庭欄位、異常白名單、is_abnormal、severity 與時間。 |
| 2 | 解析 Gateway；有 device_id 時檢查設備存在，以及設備非空的家庭及 Gateway 歸屬。 |
| 3 | 以 family_id + request_id + 正規化後 anomaly_type 組成 dedup_key。 |
| 4 | 已有 security_events 時查既有 Ledger，commit 後回 created=false、audit_log=null；不補寫第二筆稽核或事件。 |
| 5 | 新異常產生 SEC_<uuid>，寫 security_events、audit_logs、ledger_events。 |
| 6 | 原始 IP / request payload 在缺上游 hash 時雜湊；Ledger 不帶這兩個原始值，也不帶 msg。 |
| 7 | 同一 transaction 全部成功才 commit；任一步驟例外 rollback 後重新拋出。 |
| 8 | 只回報偵測與上游措施，不執行實際封鎖、鎖定或通知。 |

## API 對 SQL 寫入內容

| 資料表 | 寫入 / 更新欄位 | 觸發時機與說明 |
| --- | --- | --- |
| devices | SELECT family_id, gateway_id | 有 device_id 才檢查；本服務不修改 devices 狀態 |
| gateways / devices | resolve_gateway_id 的輔助 SELECT | 未指定 gateway_id 時嘗試解析；不是 Gateway 認證 |
| security_events | SELECT dedup_key；INSERT security_event_id, dedup_key, family_id, gateway_id, device_id, request_id, anomaly_type, severity, detected_by, reason_code, evidence_hash, occurred_at | 保存異常業務紀錄，created_at 由 DB 預設 |
| audit_logs | INSERT UC4.4_SECURITY_ANOMALY_RECORDED、parameters、status=Verified、decision、reason 與 hash chain | 共用 audit_log_service 寫入 |
| ledger_events | INSERT UC4.4 / SECURITY_ANOMALY_RECORDED / 完整 payload / payload_hash / PENDING | 與上述兩筆同一 transaction，按 dedup_key 去重 |
| notifications / control_commands | 不寫入 | 本次不建立通知送達或控制命令紀錄 |

## 共用服務 → 呼叫端 Response Packet

新紀錄回傳完整範例：
```json
{
  "created": true,
  "request_id": "GW_001_ALERT_000123",
  "anomaly_type": "ILLEGAL_CONTROL_ATTEMPT",
  "security_event_id": "SEC_<uuid>",
  "audit_log": {
    "command_id": "AUDIT_<uuid>",
    "prev_hash": "<previous_hash>",
    "current_hash": "<sha256_hex>",
    "timestamp": 1790767800
  },
  "ledger_event": {
    "event_id": "<uuid>",
    "dedup_key": "UC4.4:SECURITY_ANOMALY_RECORDED:FAMILY:12:REQUEST:GW_001_ALERT_000123:TYPE:ILLEGAL_CONTROL_ATTEMPT",
    "uc_id": "UC4.4",
    "event_type": "SECURITY_ANOMALY_RECORDED",
    "status": "PENDING",
    "payload_hash": "<sha256_hex>",
    "ledger_reference": null,
    "created": true
  }
}
```

重複回傳：created=false，request_id、anomaly_type、security_event_id 保留，audit_log=null，ledger_event 是既有完整 DB row（沒有另外補 created 欄位），可能含 payload、retry_count、時間等欄位；舊資料若缺 Ledger 則為 null。頂層 created 在新紀錄分支採用 enqueue 結果，正常一致資料下表示新增或重複。

## 去重與交易規範

```text
UC4.4:SECURITY_ANOMALY_RECORDED:FAMILY:{family_id}:REQUEST:{request_id}:TYPE:{anomaly_type}
```

相同三個值重送不新增業務、audit 或 Ledger。省略 request_id 會產生新 ID，因此不能保證重送去重；空白字串沒有在共用函式額外拒絕，呼叫端應提供穩定非空 ID。

此服務與 audit_log_service / ledger_event_service 不同：它會自己 commit / rollback。呼叫端不要將其他未提交業務放在同一 conn；重複事件分支也會 commit。函式不關閉 conn。併發仍依賴 DB UNIQUE 約束，衝突例外不會自動轉成重複成功。

## API → Gateway / ESP32 封包規範

無封包、無 ACK，也不向 App 推播。通知由 alert_service.py 在紀錄完成或失敗後另行嘗試；HTTP 入口只記錄。

## Error Responses

ApiError(status_code, code, message, detail=None) 由 HTTP 包裝成 JSON，MQTT 呼叫端則記 log 並繼續嘗試通知；本模組不輸出 CGI header。

| 狀態 | 錯誤碼 / 例外 | 情境 |
| --- | --- | --- |
| 400 | MISSING_FIELD | family_id 未提供 |
| 400 | ANOMALY_NOT_LEDGER_ELIGIBLE | 非異常白名單 |
| 400 | NOT_ABNORMAL | is_abnormal 明確為 false |
| 400 | INVALID_SEVERITY | severity 不符合四種值 |
| 400 | INVALID_TIMESTAMP | timestamp 無法解析 |
| 404 | DEVICE_NOT_FOUND | 指定設備不存在 |
| 409 | DEVICE_FAMILY_MISMATCH / DEVICE_GATEWAY_MISMATCH | 已填設備範圍不符 |
| 例外 / HTTP 500 | SQL / ValueError / TypeError 等 | 未分別封裝的 int() 轉換或 DB 錯誤向上傳遞 |

## 注意事項

- family_id 使用 int()，未額外驗證正數；有 device_id 才查設備，設備 family_id 為 NULL 時略過家庭比較。preferred gateway_id 也未另行查 DB 驗證登錄身分。
- source / actor / detection 是呼叫端資料；未新增 HTTP 登入認證、Gateway 簽章或政策引擎。
- notification_dispatched / request_blocked 是回報資訊；不能用這些欄位宣稱本服務完成通知或控制。
- 所有 `<uuid>`、`<sha256_hex>` 等為示意值，實際由程式產生。PENDING 只代表已排入待上鏈事件，真正 IOTA 提交與確認由後續 Ledger Worker 完成。
- 對外入口請參閱 [HTTP API](../security_anomaly/report_security_anomaly_README.md) 與 [MQTT 警報服務](../alert_service/alert_service_README.md)。
