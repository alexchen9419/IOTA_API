# 系統核心資料表與 API 規範文件

## 壹、核心資料表設計

在切換至 `devicemanagement` 資料庫後，系統運作共需要以下三張核心資料表：

### 1. users 表 (使用者帳號表)
用於儲存全系統使用者的基本帳號與身分驗證資訊。
| 欄位名稱 | 資料型態 | 屬性 | 說明 |
|---|---|---|---|
| `id` | INT | PK, AI | 系統內部唯一流水序號。 |
| `user_id` | VARCHAR(50) | UNIQUE, NOT NULL | 使用者選定的登入帳號(不可重複)。 |
| `username` | VARCHAR(100) | NOT NULL | 使用者姓名或暱稱。 |
| `email` | VARCHAR(100) | DEFAULT NULL | 電子郵件地址。 |
| `phone_number` | VARCHAR(20) | DEFAULT NULL | 手機號碼。 |
| `password_hash` | VARCHAR(255) | NOT NULL | 經由 bcrypt 加密後的密碼雜湊值。 |
| `role` | VARCHAR(20) | DEFAULT 'Guest' | 系統初始賦予的全域角色，預設為 'Guest'。 |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 帳號建立時間。 |

### 2. families 表 (家庭主表)
用於儲存每一個獨立家庭場域的身分與基本資訊。
| 欄位名稱 | 資料型態 | 屬性 | 說明 |
|---|---|---|---|
| `id` | INT | PK, AI | 家庭全域唯一識別碼 (`family_id`)。 |
| `family_name` | VARCHAR(100) | NOT NULL | 家庭自訂名稱(如：鶯歌老家、台北租屋處)。 |
| `admin_uid` | VARCHAR(50) | NOT NULL | 該家庭的建立者/最高權限屋主ID (Admin)。 |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 家庭建立時間。 |

### 3. user_families 表 (使用者與家庭關係中間表)
核心多對多關聯表，紀錄使用者與家庭的歸屬關係，並實作基於角色的權限控管 (RBAC)。
| 欄位名稱 | 資料型態 | 屬性 | 說明 |
|---|---|---|---|
| `id` | INT | PK, AI | 關係紀錄唯一序號。 |
| `user_id` | VARCHAR(50) | FK, NOT NULL | 關聯至 `users.user_id`，支援級聯刪除 (CASCADE)。 |
| `family_id` | INT | FK, NOT NULL | 關聯至 `families.id`，支援級聯刪除 (CASCADE)。 |
| `role` | VARCHAR(20) | DEFAULT 'Guest' | 該使用者在該特定家庭中的身分 (Admin / Member / Guest)。 |
| `joined_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 加入該家庭的時間。 |

---

## 貳、API 規範

### 一、註冊系統 API
*   **檔案路徑**：`/usr/lib/cgi-bin/register.py`
*   **請求方法**：`POST`
*   **內容類型**：`application/json; charset=utf-8`

**1. 請求內容 (Request Payload)**
```json
{
  "payload": {
    "user_id": "test_user_004",
    "username": "俊豪",
    "password": "paseword121",
    "email": "junhao@example.com",
    "phone_number": "0912345678"
  }
}
```

**2. 回應內容 (Response)**
*   **200 OK (註冊成功)**
```json
{
  "status": "Success",
  "msg": "帳號註冊成功",
  "data": {
    "id": 6,
    "user_id": "test_user_004",
    "username": "俊豪",
    "email": "junhao@example.com",
    "phone_number": "0912345678",
    "role": "Guest"
  }
}
```
*   **400 Bad Request (欄位不齊全/格式錯誤)**
```json
{"status": "Error", "msg": "欄位不齊全"}
```
*   **409 Conflict (帳號已被佔用)**
```json
{"status": "Error", "msg": "帳號 'test_user_004'已被使用，請換一個。"}
```
*   **500 Internal Server Error**
```json
{"status": "Error", "msg": "伺服器內部錯誤", "detail": "錯誤原因描述"}
```

### 二、登入 API
*   **檔案路徑**：`/usr/lib/cgi-bin/login.py`
*   **請求方法**：`POST`
*   **內容類型**：`application/json`

**1. 請求內容 (Request Payload)**
```json
{
  "payload": {
    "user_id": "test_user_004",
    "password": "paseword121"
  }
}
```

**2. 回應內容 (Response)**
*   **200 OK (登入成功)**
```json
{
  "status": "Success",
  "msg": "登入成功",
  "data": {
    "user_id": "test_user_004",
    "username": "俊豪是GAY",
    "role": "Guest",
    "families": [
      {
        "family_id": 1,
        "family_name": "實驗室",
        "user_role": "Admin"
      },
      {
        "family_id": 2,
        "family_name": "台北租屋處",
        "user_role": "Member"
      }
    ]
  }
}
```
*   **401 Unauthorized (驗證失敗)**
```json
{"status": "Error", "msg": "帳號或密碼錯誤"}
```
*   **400/500 系統異常**
```json
// 400 Bad Request
{"status": "Error", "msg": "欄位不齊全"}
// 500 Internal Server Error
{"status": "Error", "msg": "伺服器內部錯誤", "detail": "錯誤原因描述"}
```

### 三、發送家庭邀請 API
*   **路由端點**：`/cgi-bin/send_invitation.py`
*   **請求方法**：`POST`
*   **內容類型**：`application/json; charset=utf-8`

**1. 請求內容 (Request Payload)**
```json
{
  "payload": {
    "family_id": 12,
    "inviter_uid": "admin_001",
    "invitee_uid": "target_user_88",
    "role": "Member"
  }
}
```

**2. 回應內容 (Response)**
*   **200 OK / 201 Created (成功)**
```json
{
  "status": "Success",
  "msg": "邀請發送成功，等待目標使用者確認",
  "data": {
    "invitation_id": 45,
    "family_id": 12,
    "invitee_uid": "target_user_88",
    "role": "Member",
    "status": "Pending"
  }
}
```
*   **失敗回應**
    *   `403 Forbidden`: `{"status": "Error", "msg": "權限拒絕:只有該場域的Admin 才能發送邀請"}`
    *   `404 Not Found`: `{"status": "Error", "msg": "找不到該使用者，或該帳號已被系統停用"}`
    *   `409 Conflict`: `{"status": "Error", "msg": "該使用者已經是此家庭的成員，無法重複邀請"}`
    *   `422 Unprocessable Entity`: `{"status": "Error", "msg": "邀請已在待處理狀態(Pending)，請勿重複發送"}`

### 四、回應家庭邀請 API
*   **路由端點**：`/cgi-bin/respond_invitation.py`
*   **請求方法**：`POST`
*   **內容類型**：`application/json; charset=utf-8`

**1. 請求內容 (Request Payload)**
```json
{
  "payload": {
    "invitation_id": 2,
    "invitee_uid": "target_user_88",
    "action": "Accepted"
  }
}
```

**2. 回應內容 (Response)**
*   **200 OK (成功)**
```json
// 接受邀請成功
{"status": "Success", "msg": "已接受邀請，新成員權限已實時同步至地端閘道器"}
// 拒絕邀請成功
{"status": "Success", "msg": "已拒絕該家庭的邀請"}
```
*   **失敗回應**
    *   `403 Forbidden`: `{"status": "Error", "msg": "安全錯誤;您無權操作此邀請函"}`
    *   `404 Not Found`: `{"status": "Error", "msg": "找不到該邀請紀錄"}`
    *   `422 Unprocessable Entity`: `{"status": "Error", "msg": "該邀請函已被處理過或已過期"}`

### 五、更新成員權限 API
*   **路由端點**：`/cgi-bin/update_member_role.py`
*   **請求方法**：`POST`
*   **內容類型**：`application/json; charset=utf-8`

**1. 請求內容 (Request Payload)**
```json
{
  "payload": {
    "family_id": 12,
    "admin_uid": "admin_001",
    "target_uid": "target_user_99",
    "target_role": "Guest",
    "start_time": "2026-06-04 00:00:00",
    "end_time": "2026-06-05 23:59:59",
    "max_uses": 3
  }
}
```

**2. 回應內容 (Response)**
*   **200 OK (成功核發/更新/恢復/撤銷)**
```json
// 情境A：成功更新身分
{"status": "Success", "msg": "已成功將該使用者身分更新為Guest。身分異動指令已實時同步至地端閘道器"}
// 情境B：撤銷停權
{"status": "Success", "msg": "已成功撤銷該使用者所有權限(變更為Revoked)。身分異動指令已實時同步至地端閘道器"}
// 情境C：重複撤銷防呆
{"status": "Warning", "msg": "該使用者權限先前已被撤銷，無需重複操作"}
```
*   **失敗回應**
    *   `403 Forbidden`: 權限拒絕（非 Admin，或是目標帳號全域狀態非 Active）
    *   `404 Not Found`: 授權失敗（目標帳號未在平台註冊）

### 六、產生臨時訪客 QR Code 通行證 API
*   **請求路徑**：`/cgi-bin/generate_guest_qr.py`
*   **請求方法**：`POST`
*   **內容類型**：`application/json; charset=utf-8`

**1. 請求內容 (Request Payload)**
```json
{
  "payload": {
    "family_id": 12,
    "admin_uid": "admin_001",
    "start_time": "2026-07-19 15:00:00",
    "end_time": "2026-07-20 23:59:59",
    "max_uses": 1
  }
}
```

**2. 回應內容 (Response)**
*   **200 OK (成功)**
```json
{
  "status": "Success",
  "msg": "已重用閒置訪客帳號(guest_9QJInN)。權限已實時同步至地端閘道器",
  "data": {
    "user_id": "guest_9QJInN",
    "password": "產生的高強度隨機密碼",
    "control_url": "https://your-domain.com/qr-control?uid=guest_9QJInN&pwd=產生的高強度隨機密碼"
  }
}
```
*   **失敗回應**
    *   `403 Forbidden`: `{"status": "Error", "msg": "權限拒絕:只有該場域的Admin才能產生訪客條碼"}`
    *   `400 Bad Request`: `{"status": "Error", "msg": "核心欄位(family_id, admin_uid)不齊全"}`
