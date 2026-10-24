# POST /change_password

使用者修改自己的密碼。

目前 API 沒有 Token/Session，身分只靠 payload 的 `user_id`，所以**必須驗舊密碼**；忘記密碼的重設流程不在這支範圍。

## Request

```json
{
  "payload": {
    "user_id": "u001",
    "old_password": "舊密碼",
    "new_password": "新密碼"
  }
}
```

## Response

```json
{ "status": "Success", "msg": "密碼已更新", "data": { "user_id": "u001" } }
```

| HTTP | msg | 情況 |
|---|---|---|
| 400 | 欄位不齊全 | 三個欄位缺一或不是字串 |
| 400 | 新密碼至少需要 8 個字元 | |
| 400 | 新密碼過長（上限 72 bytes） | bcrypt 只看前 72 bytes |
| 400 | 新密碼不可與舊密碼相同 | |
| 401 | 帳號或舊密碼錯誤 | 帳號不存在也回這個，不洩漏帳號是否存在 |
| 403 | 此帳號已被系統停用，請聯絡管理員 | `users.status` 不是 `Active` |
| 405 | 僅支援 POST 請求方法 | |
| 500 | 資料庫操作失敗 / 伺服器內部錯誤 | |

## 資料庫

- `users.password_hash` 更新為新的 bcrypt 雜湊（與 `register` 相同演算法）。
- 成功時在同一交易寫一筆 `audit_logs`：`action=PASSWORD_CHANGED`、`actor_type=USER`、`reason=USER_SELF_SERVICE`，`parameters` 只有 `target_uid`，**不含任何密碼或雜湊**。
- 失敗不寫稽核（與 `login` 一致）。
- 不需要改 schema。

## 已知限制

- 沒有失敗次數限制，跟 `login` 一樣可以被暴力嘗試舊密碼。
- 改完密碼不會讓其他已登入的 App 登出（目前也沒有 Session 可以撤銷）。
