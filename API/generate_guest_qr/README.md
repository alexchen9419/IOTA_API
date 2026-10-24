# 產生臨時訪客 QR Code 通行證 API 的規範

## 基本資訊

| 項目 | 內容 |
| --- | --- |
| 實作檔案 | `API/generate_guest_qr/generate_guest_qr.py` |
| 對應 UC | UC3.4 核發訪客通行碼 → UC4.2 訪客掃碼開門 |
| 方法 | POST；非 POST 回 405 |
| Endpoint | `POST http://<host>:8091/generate_guest_qr`（`API/gateway.py`） |
| 憑證 | `guest_tokens`（DB 只存 SHA-256），**不再建立 guest_ 訪客帳號** |
| Ledger Event | GUEST_TOKEN_ISSUED，credential_kind=GUEST_TOKEN_QR |

### 為什麼不再用訪客帳號

舊版建立 `guest_` 帳號、把明文密碼放在 `control_url` 的 query string（`?uid=...&pwd=...`）。兩個問題：

1. `control_device.py` 的帳號驗證只放行 Admin/Member，訪客帳號**根本開不了門**；能讓訪客開門的是 `auth_type=guest_token`
2. query string 會留在瀏覽器歷史、伺服器 access log、Referer 裡

現在改發 guest token，網址 `<base>/guest#t=GUEST_...`：token 放 URL fragment，fragment 不會隨 HTTP 請求送出。訪客頁讀出後就從網址列移除。

## 掃碼開門流程

```
App → POST /generate_guest_qr → control_url（QR 內容）
訪客手機掃碼 → GET /guest（gateway.py 提供的靜態頁 API/guest_page/guest.html）
  → 頁面 JS 讀 #t= → POST /guest_access {guest_token}   （查可開的門、剩餘次數，不扣次）
  → 按「開門」→ POST /control_device {auth_type: guest_token, guest_token, family_id, device_id, action: UNLOCK}
```

`control_device.py` 檢查撤銷、`valid_from`、`expires_at`、次數、場域、裝置、動作，成功才扣次。寫入 `audit_logs`／`control_commands` 的請求副本會把 `guest_token` 遮蔽成 `<redacted>`。

## 網址設定

| 設定 | 結果 |
| --- | --- |
| `GUEST_QR_BASE_URL=https://home.example.org/guest` | 用這個網址（正式環境建議設、且用 HTTPS） |
| 未設定 | 用 App 呼叫 API 時的 Host，例如 `http://192.168.1.3:8091/guest`——只有跟閘道同一個區網的手機打得開 |

區網 HTTP 下 token 是明文傳輸（同 Wi-Fi 的人可側錄），對外開放前務必走 HTTPS。

## POST Request Parameters

| 欄位 | 型別 | Required | 說明 |
| --- | --- | --- | --- |
| family_id | integer | true | 正整數家庭 ID |
| admin_uid | string | true | 此家庭 Admin |
| device_id | string | false | 只開放這台裝置；省略＝家庭內所有裝置 |
| start_time | string / null | false | ISO 8601 或 `YYYY-MM-DD HH:MM:SS`（無時區視為台灣時間），省略為現在 |
| end_time | string / null | 條件式 | 與 max_uses 至少一個；省略時有效期為 `GUEST_QR_DEFAULT_HOURS`（預設 24 小時） |
| max_uses | integer / null | 條件式 | 正整數；省略＝不限次數（DB 存 2147483647，回應顯示 null） |

允許動作固定為 `["UNLOCK"]`（門鎖 5 秒後自動上鎖）。

## Response（HTTP 200）

```json
{
  "status": "Success",
  "msg": "已核發訪客通行碼，請將 QR 提供給訪客",
  "data": {
    "control_url": "http://192.168.1.3:8091/guest#t=GUEST_<random>",
    "authorization_id": "GT_20261024_<hex>",
    "device_id": null,
    "allowed_actions": ["UNLOCK"],
    "start_time": "2026-10-24T07:00:00Z",
    "end_time": "2026-10-24T11:00:00Z",
    "max_uses": 3,
    "audit_log": {"...": "..."},
    "ledger_event": {"...": "..."}
  }
}
```

回應的 `start_time`／`end_time` 是 UTC（帶 `Z`）。請求裡**不帶時區的時間會當台灣時間**——App 的 `formatForApi()` 輸出的是不帶時區的 UTC，不能拿來呼叫這支，要送帶 `Z` 的 ISO 字串。

明文 token 只出現在這次回應的 `control_url`。`authorization_id` 就是 `guest_tokens.token_id`；之後用 `POST /list_guest_tokens` 查、`POST /revoke_guest_token` 撤銷（UC3.5）。

## Error Responses

| HTTP | 情境 |
| --- | --- |
| 400 | 空本文 / JSON / 缺欄位 / 時間或次數不合法 |
| 403 | 不是該家庭 Admin |
| 404 | 家庭不存在、device_id 不屬於此家庭 |
| 405 | 非 POST |
| 500 | DB 錯誤；未設 `GUEST_QR_BASE_URL` 又拿不到 Host；舊 DB 缺 `guest_tokens.valid_from` 卻指定未來開始時間 |

## 注意事項

- 不再發布 `home/security/gateway_{family_id}/auth_sync`（原本就沒有 subscriber，而且內容是已經不存在的訪客帳號）。
- 訪客頁（`/guest`、`/guest_access`）跟 API 同一個 port，訪客手機必須連得到 8091。
- `admin_uid` 仍是 payload 自報，沒有綁定登入身分（跟其他端點相同的既有限制）。
