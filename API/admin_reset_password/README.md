# POST /admin_reset_password

成員忘記密碼時，由所在場域的 Admin 協助重設。後端產生一次性臨時密碼，成員用它登入後 App 會強制設定新密碼。

目前 API 沒有 Token/Session，身分只靠 payload，所以權限刻意收緊（見下方限制）。

## Request

```json
{
  "payload": {
    "family_id": 12,
    "admin_uid": "admin001",
    "target_uid": "u001"
  }
}
```

## Response

```json
{
  "status": "Success",
  "msg": "已重設密碼，請將臨時密碼交給成員，對方登入後需設定新密碼",
  "data": { "target_uid": "u001", "temp_password": "Kp7mXq2Rva" }
}
```

臨時密碼只出現在這次回應，DB 只存 bcrypt 雜湊；10 個字元，不含容易看錯的 0/O、1/l/I。

| HTTP | msg | 情況 |
|---|---|---|
| 400 | 欄位不齊全 / family_id 格式錯誤 | |
| 400 | 不能重設自己的密碼，請使用「修改密碼」 | admin_uid = target_uid |
| 403 | 權限拒絕：只有該場域的管理員可以重設成員密碼 | admin_uid 不是該場域 Admin |
| 403 | 無法重設管理員的密碼… | target_uid 在**任何**場域是 Admin |
| 403 | 此帳號已被系統停用，無法重設 | `users.status` 不是 `Active` |
| 404 | 此帳號不是這個場域的成員 | 不在場域內或角色是 Revoked |
| 500 | 資料庫缺少 users.must_change_password 欄位… | 舊 DB 沒跑 migration |

## 限制與理由

- **不能重設任何管理員**：避免 A 家的 Admin 藉由共同成員關係接管 B 家 Admin 的帳號。管理員忘記密碼由維運人員在伺服器端處理。
- **只能重設同場域、未撤銷的成員**。
- 一定要有 `must_change_password` 欄位才會執行，避免臨時密碼變成永久密碼。

## 資料庫

- `users.password_hash` 換成臨時密碼的 bcrypt 雜湊，`users.must_change_password = 1`。
- 同一交易寫 `audit_logs`：`action=PASSWORD_RESET_BY_ADMIN`、`actor_id=admin_uid`、`family_id`、`reason=ADMIN_ASSISTED_RESET`，`parameters` 只有 `target_uid`。
- `/login` 會在 `data.must_change_password` 回傳這個旗標；`/change_password` 成功後歸 0。
- 舊 DB 請執行 `API/migrations/2026-10-28_users_must_change_password.sql`。
