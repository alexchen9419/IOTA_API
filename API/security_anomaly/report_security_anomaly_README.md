# 安全異常回報 API 的規範

依 report_security_anomaly.py 的共用服務串接版撰寫，沿用既有 API README 章節與封包格式。

## 基本資訊

| 項目 | 內容 |
| --- | --- |
| 實作檔案 | `IOTA_API/API/security_anomaly/report_security_anomaly.py` |
| 對應 UC | UC4.4 |
| 方法 | POST / CGI stdin；此檔讀 stdin，未自行強制拒絕所有非 POST |
| Endpoint | `/cgi-bin/report_security_anomaly.py`（部署映射，實際依 CGI 設定） |
| Content-Type | application/json; charset=utf-8 |
| 呼叫端 | Gateway / Policy Engine / Heartbeat Monitor / Server 安全模組 |
| 共用紀錄 | record_security_anomaly(conn, req) |
| App / ESP 互動 | 本入口只記錄，不發通知或控制封包 |

## POST Request Parameters

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

其他驗證、白名單及 hash 規則與 [security_anomaly_service_README.md](../common/security_anomaly_service_README.md) 一致。

## Gateway / 安全模組 → API Request Packet

接受 `{ "payload": {...} }` 或異常 object 直接置頂；不接受 MQTT topic 作為本文。完整輸入示例：
```json
{
  "payload": {
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
}
```

## Processing Rules

| 編號 | 規則 |
| --- | --- |
| 1 | read_payload() 讀取 stdin；空本文、非 JSON 或不是 object 回 400。 |
| 2 | get_conn() 依 DB 環境變數建立 DictCursor、autocommit=False 專用連線。 |
| 3 | 直接呼叫共用紀錄服務，讓其驗證、去重及共同提交三張表。 |
| 4 | 取出 result.created 作 HTTP 狀態判斷：新事件 201，重複 200；回應 data 不包含這個頂層 created。 |
| 5 | ApiError 使用原 status_code / code / message / detail；JSONDecodeError 回 INVALID_JSON；其他例外回 500 INTERNAL_ERROR。 |
| 6 | finally 關閉 DB connection；本入口不自行發 MQTT，也不等待 IOTA。 |

## API 對 SQL 寫入內容

| 資料表 | 寫入 / 更新欄位 | 觸發時機與說明 |
| --- | --- | --- |
| devices | SELECT family_id, gateway_id | 有 device_id 才檢查；本服務不修改 devices 狀態 |
| gateways / devices | resolve_gateway_id 的輔助 SELECT | 未指定 gateway_id 時嘗試解析；不是 Gateway 認證 |
| security_events | SELECT dedup_key；INSERT security_event_id, dedup_key, family_id, gateway_id, device_id, request_id, anomaly_type, severity, detected_by, reason_code, evidence_hash, occurred_at | 保存異常業務紀錄，created_at 由 DB 預設 |
| audit_logs | INSERT UC4.4_SECURITY_ANOMALY_RECORDED、parameters、status=Verified、decision、reason 與 hash chain | 共用 audit_log_service 寫入 |
| ledger_events | INSERT UC4.4 / SECURITY_ANOMALY_RECORDED / 完整 payload / payload_hash / PENDING | 與上述兩筆同一 transaction，按 dedup_key 去重 |
| notifications / control_commands | 不寫入 | 本次不建立通知送達或控制命令紀錄 |

## API → 呼叫端 Response Packet

首次成功 HTTP 201：
```json
{
  "status": "Success",
  "message": "Security anomaly recorded and ledger event queued.",
  "data": {
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
}
```

重複 HTTP 200，message 為 "Security anomaly was already recorded; no duplicate audit or ledger event was created."；data 回 request_id、anomaly_type、security_event_id、audit_log=null、ledger_event。重複的 ledger_event 為完整 DB row 或 null，而非固定的新建 metadata 結構。

原 HTTP 請求格式保留；新增成功也會回傳 security_event_id。data.ledger_event.created 是 enqueue 新建 metadata 的欄位，與 HTTP 移除的頂層 created 不是同一層。

## API → Gateway / ESP32 封包規範

| 方向 | 規範 |
| --- | --- |
| API → 呼叫端 | 僅 HTTP JSON 業務結果與事件 metadata |
| API → App 推播 | 不發布 app/user/.../notifications |
| API → Gateway / ESP32 控制 | 不下發控制或封鎖命令，不等待 ACK |
| 需要通知 | Gateway 發警報 MQTT 由 alert_service.py 處理，或由其他通知流程完成 |

## Error Responses

錯誤本文範例：
```json
{
  "status": "Error",
  "code": "ANOMALY_NOT_LEDGER_ELIGIBLE",
  "message": "anomaly_type is not in the ledger whitelist.",
  "detail": null
}
```

| HTTP 狀態 | 錯誤碼 | 說明 |
| --- | --- | --- |
| 400 | EMPTY_BODY / INVALID_JSON / INVALID_PAYLOAD | HTTP 本文錯誤 |
| 400 | MISSING_FIELD | family_id 未提供 |
| 400 | ANOMALY_NOT_LEDGER_ELIGIBLE | 非異常白名單 |
| 400 | NOT_ABNORMAL | is_abnormal 明確為 false |
| 400 | INVALID_SEVERITY | severity 不符合四種值 |
| 400 | INVALID_TIMESTAMP | timestamp 無法解析 |
| 404 | DEVICE_NOT_FOUND | 指定設備不存在 |
| 409 | DEVICE_FAMILY_MISMATCH / DEVICE_GATEWAY_MISMATCH | 已填設備範圍不符 |
| 500 | INTERNAL_ERROR | DB 或未另外捕捉的型別轉換 / 其他錯誤 |

錯誤形狀固定 status、code、message、detail。family_id 或巢狀 int() 格式錯誤可能進入 500，不能宣稱每種輸入錯誤都會有 400。

## 環境變數

| 變數 | 預設 / 行為 | 說明 |
| --- | --- | --- |
| DB_HOST | localhost | MySQL 位址 |
| DB_USER | vboxuser | MySQL 帳號 |
| DB_PASS | 無內建預設 | 由部署環境提供 |
| DB_NAME | devicemanagement | 此 HTTP 程序使用的 DB |
| DEBUG | 0 | 1 時 INTERNAL_ERROR detail 使用 traceback；其他值為例外字串 |

原設定未修改；alert_service.py 的 DB_NAME 原預設不同，要共用紀錄及去重，需讓兩個程序環境變數指向同一資料庫。

## 注意事項

- 重送必須沿用 request_id；HTTP 未提供 ID 時每次產生新 ID。
- HTTP 的 notification 欄位採用上游回報；notification_dispatched=true 不表示本 API 發了通知。
- 異常分類不是來源身分驗證；本檔不驗證 Bearer Token、密碼或 Gateway 簽章。
- 所有 `<uuid>`、`<sha256_hex>` 等為示意值，實際由程式產生。PENDING 只代表已排入待上鏈事件，真正 IOTA 提交與確認由後續 Ledger Worker 完成。

## 呼叫範例

```bash
curl -X POST "http://localhost:8000/cgi-bin/report_security_anomaly.py" \
  -H "Content-Type: application/json" \
  -d '{"payload":{"family_id":12,"request_id":"GW_001_ALERT_000123","gateway_id":"GW_001","device_id":"ESP32_LOCK_001","anomaly_type":"ILLEGAL_CONTROL_ATTEMPT","severity":"HIGH","timestamp":"2026-09-30T19:30:00+08:00"}}'
```

此入口可先只提供必要欄位，但裝置 / Gateway 範例需在 DB 中有相應資料。直接 CGI 測試可把相同 JSON pipe 至 security_anomaly/report_security_anomaly.py；不代表伺服器已自動建立上述 endpoint。
