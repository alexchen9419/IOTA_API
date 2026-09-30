# MQTT 警報路由與 App 分權通知的規範

依 alert_service.py 串接版撰寫，沿用既有 MQTT Worker README 的 Topic、Payload、SQL、環境變數及啟動格式。

## 基本資訊

| 項目 | 內容 |
| --- | --- |
| 實作檔案 | `IOTA_API/API/alert_service/alert_service.py` |
| 對應 UC | UC4.4 |
| 執行方式 | 常駐 MQTT subscriber；不是 HTTP endpoint |
| 用途 | 接收家庭 Gateway 警報，先嘗試紀錄，再向同家庭 Admin / Member 推播 |
| App 互動 | 發布 app/user/{user_id}/notifications |
| Gateway / ESP 互動 | 接收 Gateway alert；不下發 ESP32 控制指令 |
| 共用紀錄 | common/security_anomaly_service.py |
| Broker 連線 | 保留原 mqtt.Client()；固定 TCP 1883、keepalive 60，未使用 mqtt_tls |

## Gateway → MQTT Service 封包規範

| 項目 | 內容 |
| --- | --- |
| Topic Filter | home/security/+/alert |
| Topic Example | home/security/gateway_12/alert |
| 實際允許 Topic | 完整比對 home/security/gateway_([0-9]+)/alert；其他匹配訂閱但格式不符的 Topic 會拒絕 |
| 家庭 ID | gateway_12 的 12 是 family_id；不是資料表 gateway_id=GW_001 |
| Payload Format | UTF-8 JSON object，直接置頂，沒有 HTTP 的 payload 外層 |
| Subscription QoS | 1 |

```json
{
  "request_id": "GW_001_ALERT_000123",
  "gateway_id": "GW_001",
  "device_id": "ESP32_LOCK_001",
  "anomaly_type": "ILLEGAL_CONTROL_ATTEMPT",
  "severity": "HIGH",
  "timestamp": "2026-09-30T19:30:00+08:00",
  "msg": "偵測到未授權的門鎖控制請求"
}
```

## MQTT Payload Parameters

| 欄位 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| request_id | string | 紀錄必須 | 穩定的同一事件 ID，重送沿用；通知本身可省略 |
| anomaly_type | string | 紀錄必須 | 共用服務異常白名單；通知本身可省略 |
| family_id | integer / string | false | 取 Topic 整數 ID；若提供，以去除空白的字串與 Topic 正規化值比對，不一致整個事件拒絕 |
| msg | string | false | 印 log 並沿原 Payload 通知；不進共用 Ledger Payload |
| gateway_id / device_id | string | false | 有 device_id 時套用共用設備歸屬檢查 |
| severity / timestamp | string | false | HIGH / 現在 UTC 預設，完整規則見共用服務 |
| actor_id / actor_type / actor_role | string / null | false | 依共用服務使用；沒有 UC4.5 的 actor 物件簽章格式 |
| detection / source_evidence / system_action / notification | object | false | 四個巢狀區塊，規則見共用服務 |
| source | string | false / 紀錄覆蓋 | 紀錄固定 GATEWAY；發給 App 的原 Payload 不因此改寫 |

## Processing Rules

| 編號 | 規則 |
| --- | --- |
| 1 | on_connect rc=0 訂閱 home/security/+/alert，QoS 1；失敗只記錄連線回傳碼。 |
| 2 | on_message 完整比對 Topic，解析 family_id；JSON 必須是 object。 |
| 3 | Payload 有 family_id 時需與 Topic 一致；Topic / JSON / family 不符時不紀錄、不通知。 |
| 4 | request_id 及 anomaly_type 轉字串去空白後都非空才呼叫共用服務；缺任一欄位保留舊通知流程，印略過紀錄原因。 |
| 5 | 紀錄使用 Topic family_id、source=GATEWAY；notification_dispatched 強制 false，notified_roles 強制 [ADMIN, MEMBER]，因為本次通知尚未發送。 |
| 6 | 共用服務完成 commit / rollback 後關閉紀錄連線；驗證或 DB 失敗記 log，但仍嘗試查收件人與通知。 |
| 7 | 另開 DB 連線讀 user_families 的同家庭 Admin、Member；不通知 Guest 或其他家庭。此查詢未另驗 users.status。 |
| 8 | 逐收件人建立 Payload copy，追加 notification_target，發布 app/user/{uid}/notifications、QoS 1。 |
| 9 | 檢查 publish info.rc，成功 log 只表示排入 MQTT 發送佇列；異常或非成功 rc 印失敗，繼續其他收件人。 |
| 10 | 重複異常不新增三張表，但通知仍重送；不在 on_message 呼叫 wait_for_publish，也不修改已入庫 Ledger Payload。 |

## API 對 SQL 寫入內容

| 資料表 | 寫入 / 更新欄位 | 觸發時機與說明 |
| --- | --- | --- |
| security_events / audit_logs / ledger_events | 透過共用服務 SELECT / INSERT | 三張表同一紀錄交易，完整欄位見共用服務文件 |
| user_families | SELECT user_id, role WHERE family_id=%s AND role IN (Admin, Member) | 只用來查通知對象，另開連線 |
| users | 不查詢 | 目前不以全域 Active / Disabled 狀態過濾通知 |
| notifications / control_commands / devices 狀態 | 不寫入 | 無通知回執、無設備控制或送達紀錄 |

## MQTT Service → App Notification Packet

Topic：`app/user/admin001/notifications`，QoS 1。通知包含收到的原始 Payload，再加 notification_target；每位收件人使用獨立 dict。
```json
{
  "request_id": "GW_001_ALERT_000123",
  "gateway_id": "GW_001",
  "device_id": "ESP32_LOCK_001",
  "anomaly_type": "ILLEGAL_CONTROL_ATTEMPT",
  "severity": "HIGH",
  "timestamp": "2026-09-30T19:30:00+08:00",
  "msg": "偵測到未授權的門鎖控制請求",
  "notification_target": "admin001"
}
```

原 Payload 的 source / notification 欄位即使被紀錄分支覆蓋，通知仍保留原內容；本服務不將新增 security_event_id / ledger_event 自動附入通知。通知 Payload 也沒有自動刪除 source_evidence 原始值；Gateway 應只發布允許家庭成員接收的警報內容。

## 舊格式相容與失敗行為

```json
{
  "msg": "偵測到門鎖警報"
}
```

| 情況 | 異常紀錄 | App 通知 |
| --- | --- | --- |
| 合法新格式 | 共用服務入庫、PENDING Ledger | 向查到的 Admin / Member 嘗試推播 |
| 相同家庭/request/type 重送 | 不重複新增 | 仍重送 |
| 缺 request_id 或 anomaly_type | 略過並印原因 | 仍嘗試推播 |
| 非白名單 / severity 或其他紀錄驗證失敗 | 回滾 / 不完成紀錄 | 仍嘗試推播 |
| 紀錄 DB 連線或表失敗 | 不完成紀錄、印失敗 | 仍嘗試查收件人 / 推播 |
| Topic / JSON 不合法或家庭不一致 | 無 | 無 |
| 無 Admin / Member | 已完成紀錄保留 | 無 |
| 收件人查詢 SQL 失敗 | 已完成紀錄保留 | 查詢 helper 記 DB 錯誤並回空清單，不發通知 |
| 收件人 DB 連線建立失敗 | 已完成紀錄保留 | 外層印處理錯誤，不發通知 |
| publish rc 失敗 / 例外 | 已提交紀錄保留 | 印失敗，繼續其餘收件人 |

## Error Responses

沒有 HTTP Error JSON；輸出至 stdout 的 log 包含：

| Log | 意義 |
| --- | --- |
| [拒絕警報] | Topic / Payload object / 家庭驗證不符 |
| [略過異常紀錄] | 沒有紀錄必需的 request_id 或 anomaly_type |
| [異常紀錄] 新增 / 已存在 | 共用紀錄結果，附 security_event_id |
| [異常紀錄失敗] | 本次紀錄未完成，仍嘗試通知 |
| [資料庫錯誤] | 收件人 SQL 或紀錄連線關閉錯誤 |
| 已排入 MQTT 發送佇列 | publish rc=MQTT_ERR_SUCCESS，未確認 App 收到 |
| MQTT 排入失敗 | 非成功 rc 或 publish 例外 |
| [處理錯誤] | JSON / UTF-8 / 收件人連線等其他處理例外 |

本版沒有自動補寫、重試佇列、通知 outbox 或送達 ACK；失敗後要修正原因並重送穩定 request_id。

## 環境變數

| 變數 | 原預設 / 行為 | 說明 |
| --- | --- | --- |
| DB_HOST | localhost | MySQL 位址 |
| DB_USER | vboxuser | MySQL 帳號 |
| DB_PASS | 沿用原程式 fallback | 可用環境變數覆蓋；此文件不複製既有明文預設密碼 |
| DB_NAME | database02 | 與 report_security_anomaly.py 的 devicemanagement 預設不同 |
| MQTT_HOST | 192.168.0.84 | 原 Broker 位址 |
| MQTT_PORT / MQTT_USE_TLS / MQTT_CA_CERT | 本檔未讀取 | 固定 port 1883，沒有 tls_set 或 mqtt_tls 整合 |
| MQTT_USERNAME / MQTT_PASSWORD | 本檔未讀取 | 未設定 Broker 使用者帳密 |

環境變數名稱和預設值未更動。兩個入口要共用同一筆紀錄及去重，應以 DB 環境變數指向相同資料庫，且使用既有完整 schema.sql。

## 注意事項

- Topic 的 + 必須佔完整一層；原 gateway_+ 不是有效 wildcard 用法。
- notification_dispatched=false 是入庫時點的狀態，不是說後續永遠不推播；本版不回寫通知結果到已算 hash 的事件。
- rc 成功不是 broker / App 最終送達回執；QoS 1 也不能當成「通知僅一次」。
- DB 操作同步執行在 MQTT callback，沒有背景工作池。
- 接受 Topic 與家庭一致不等於驗證發送者；此檔未新增 Gateway 簽章或 Broker ACL。
- 不發 ESP32 控制或封鎖指令；來源資料需由 Gateway 的偵測流程提供。
- 共用驗證與欄位詳細規範見 [security_anomaly_service_README.md](../common/security_anomaly_service_README.md)。

## 啟動範例

在 API 根目錄執行；依部署環境安裝 pymysql、python-dotenv、paho-mqtt。

```bash
export DB_NAME=devicemanagement
export MQTT_HOST=192.168.0.84
python3 -u alert_service/alert_service.py
```

DB_NAME 示例用於讓 HTTP 與 MQTT 程序指向同一 DB，並非修改程式預設值；DB_PASS 請透過部署環境提供。此服務需要常駐，否則無法消費 Gateway 的 alert 訊息。
