# 設備除役 API 與 ESP32 互動規範

以原 `decommission_device/README.md` 為基礎，依修正後程式更新欄位、權限、憑證撤銷、回應及 Ledger Event。

## 基本資訊

| 項目 | 內容 |
| --- | --- |
| 對應檔案 | `IOTA_API/API/decommission_device/decommission_device.py` |
| 對應 UC | UC2.3 |
| 方法 | POST / CGI stdin；程式未自行檢查 REQUEST_METHOD |
| Endpoint | `/decommission_device/decommission_device.py`（部署映射） |
| App 互動 | 設備所屬家庭 Admin 發起除役 |
| ESP 互動 | 目前只更新 DB，沒有 MQTT 下發或 ESP32 ACK 處理 |
| Ledger Event | DEVICE_DECOMMISSIONED |

## POST Request Parameters

| 欄位 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| operator_user_id | string | true（或別名） | 依序讀 operator_user_id、admin_user_id、user_id；不可省略 |
| device_id | string | true | 目標設備 ID |
| family_id | integer | false | 由 devices 取實際家庭；提供時必須一致 |
| reason | string | false | 預設 UC2.3 device decommission |
| reason_code | string | false | 預設 ADMIN_DEVICE_DECOMMISSION，轉大寫 |
| auth_type | string | false / 不使用 | 不參與此檔驗證；不支援 guest_token 除役 |

## App → API Request Packet

支援 payload 包裝或直接 object。
```json
{
  "payload": {
    "operator_user_id": "admin001",
    "family_id": 12,
    "device_id": "ESP32_LOCK_001",
    "reason": "汰換舊設備",
    "reason_code": "ADMIN_DEVICE_DECOMMISSION"
  }
}
```

## Processing Rules

| 編號 | 規則 |
| --- | --- |
| 1 | 查 devices 並鎖定目標；不存在時可寫拒絕稽核並 commit，回 404。 |
| 2 | 從設備取得 family_id；缺少家庭或請求家庭不一致回 409。 |
| 3 | require_family_admin 查 user_families，只允許設備家庭 Admin。 |
| 4 | 已 Revoked / Retired / Decommissioned 時回 200，不建立第二筆除役 Ledger Event，但會新增 NOOP 稽核。 |
| 5 | 更新 status=Revoked、pairing_status=unpaired、session_key_hash=NULL、last_action、撤銷資訊；不刪除 devices。 |
| 6 | 撤銷 CRED_<device hash> 憑證；若舊設備無憑證，需有 device_public_key 才能補建為 Revoked。 |
| 7 | 寫稽核 UC2.3_DEVICE_DECOMMISSION 及 UC2.3 DEVICE_DECOMMISSIONED；設備、憑證與事件同交易提交。 |
| 8 | 成功只代表 Server 資料及待上鏈事件完成，不代表 ESP32 已執行除役。 |

## API 對 SQL 寫入內容

| 資料表 | 寫入 / 更新欄位 | 觸發時機與說明 |
| --- | --- | --- |
| devices | SELECT / UPDATE status, pairing_status, session_key_hash, last_action, revoked_at, revoked_by, revocation_reason | 存在的欄位才更新；設備資料保留 |
| user_families | SELECT role | 依設備實際 family_id 驗證 Admin |
| device_credentials | INSERT / UPDATE status=Revoked, revoked_at, revoked_by, revocation_reason | 缺表或缺憑證及公鑰導致失敗 |
| audit_logs | INSERT UC2.3_DEVICE_DECOMMISSION / DENIED / NOOP | 既有內部 writer；若表缺失可略過，詳見 written=false |
| device_telemetry | 條件式 INSERT device_id, status=Revoked, telemetry_data | 表與必要欄位存在才寫 |
| ledger_events | INSERT DEVICE_DECOMMISSIONED，PENDING | dedup_key=UC2.3:DEVICE_DECOMMISSIONED:FAMILY:{family_id}:DEVICE:{device_id} |
| control_commands | 不寫入 | 目前無 MQTT 下發或 ESP ACK 流程 |

## API → App Response Packet

新除役成功 HTTP 200；DB 狀態用 Revoked，Ledger Payload 的 new_status 用 DECOMMISSIONED。
```json
{
  "status": "Success",
  "msg": "UC2.3 終端設備除役、安全解綁與 Ledger Event 建立完成",
  "data": {
    "device_id": "ESP32_LOCK_001",
    "previous_status": "Active",
    "new_status": "Revoked",
    "previous_pairing_status": "paired",
    "new_pairing_status": "unpaired",
    "session_key_hash_revoked": true,
    "credential_revocation": {
      "credential_id": "CRED_<device_hash_prefix>",
      "credential_id_hash": "sha256:<hash>",
      "revocation_status": "REVOKED"
    },
    "audit": {
      "written": true,
      "command_id": "UC2.3_DEVICE_DECOMMISSION-<timestamp>-<random>",
      "prev_hash": "<previous_hash>",
      "current_hash": "<sha256_hex>",
      "timestamp": 1790767800
    },
    "ledger_event": {
      "event_id": "<uuid>",
      "dedup_key": "UC2.3:DEVICE_DECOMMISSIONED:FAMILY:12:DEVICE:ESP32_LOCK_001",
      "uc_id": "UC2.3",
      "event_type": "DEVICE_DECOMMISSIONED",
      "status": "PENDING",
      "payload_hash": "<sha256_hex>",
      "ledger_reference": null,
      "created": true
    }
  }
}
```

重複除役的 data 回傳 device_id、previous_status、new_status、previous_pairing_status、audit、ledger_event；不含新的 credential_revocation，既有 Ledger 可能為 null。

## API → ESP32 Command Packet

目前程式未發布 DECOMMISSION MQTT 命令；舊文件中的命令 JSON 是設計示例，不列為本版已實作 I/O。

## ESP32 → API ACK Packet

本 API 不接收或等待 ESP32 ACK，也不更新 control_commands；設備端停用與金鑰清除需另行整合。

## Error Responses

| HTTP 狀態 | 錯誤碼 / 情境 | 說明 |
| --- | --- | --- |
| 400 | 缺欄位 / JSON / SQL DataError 或 IntegrityError | 使用 status=Error、msg；SQL 錯誤可能含 detail |
| 403 | 非設備家庭 Admin | require_family_admin 拒絕 |
| 404 | 設備不存在 | 回應可包含 audit |
| 409 | family_id 缺失或不一致 | 不能建立場域事件 |
| 500 | 缺表 / 憑證資料不足 / 內部錯誤 | 回 status、msg、可能 detail；不是統一 code 格式 |

## 注意事項

- 原文件的 user_id 可透過別名沿用，建議明確使用 operator_user_id；family_id 已改為可省略。
- role 查詢不是 HTTP 使用者認證；本版未驗證密碼或 Bearer Token。
- NOOP 仍產生稽核，僅 Ledger 去重；不要宣稱重送完全不新增日誌。
- 所有範例中的 `<uuid>`、`<sha256_hex>`、`<previous_hash>` 等為示意值；實際值由程式產生。Ledger Event 的 `PENDING` 只代表待上鏈紀錄已建立，不代表 IOTA 已確認。

## App 呼叫範例

正式部署請使用 HTTPS；以下為本機 CGI 路由示例，實際路由需與伺服器設定一致。

```bash
curl -X POST "http://localhost:8000/decommission_device/decommission_device.py" \
  -H "Content-Type: application/json" \
  -d '{"payload":{"operator_user_id":"admin001","family_id":12,"device_id":"ESP32_LOCK_001","reason":"汰換舊設備","reason_code":"ADMIN_DEVICE_DECOMMISSION"}}'
```
