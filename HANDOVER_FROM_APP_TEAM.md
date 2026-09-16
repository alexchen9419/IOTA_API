# App 端對後端所做的變更 — 交接說明

**日期**：2026-08-17
**範圍**：為了讓 Flutter App（`app/`）能實際運作，動到了後端與環境設定。
本文件列出**所有不屬於 App 團隊負責範圍**、但被修改或新增的檔案，以及原因。

---

## TL;DR

| 類別 | 數量 | 影響 |
| :-- | :-: | :-- |
| 修改既有後端檔案 | 2 支 | `gateway.py`（新增路由 + CORS）、`list_devices.py`（修跨場域外洩） |
| 新增後端端點 | 5 支 | App 一直在呼叫但後端不存在的 3 支 + 建立場域 + 輪詢用的 `get_command_status`（見第二節） |
| 新增開發環境設定 | 2 份 | 本機開發用，**沒有**覆蓋任何既有設定或憑證 |
| 刪除既有檔案 | 0 | 無 |

**沒有動到**：`Arduino/`、`mqtt-server/`、`config/certs/`、`config/mosquitto.conf`、
`docker-compose.yml`、`API/schema.sql`，以及其餘所有 CGI 腳本。

---

## 一、修改的既有後端檔案

### 1. `API/gateway.py`

原負責範圍：API 閘道（見 `API/WHITEPAPER.md` 第 2 節）。

**變更 A — 新增 12 條路由**

```
+ create_family                    （新增端點，見第二節）
+ get_user_families                （新增端點，見第二節）
+ get_family_members               （新增端點，見第二節）
+ get_invitations                  （新增端點，見第二節）
+ gateway_initialize               （UC1.3，腳本早已存在）
+ gateway_initialization_status     （UC1.3，腳本早已存在）
+ create_gateway_trust             （UC1.4，腳本早已存在）
+ confirm_gateway_trust            （UC1.4，腳本早已存在）
+ list_gateway_trusts              （UC1.4，腳本早已存在）
+ revoke_gateway_trust             （UC1.4，腳本早已存在）
+ get_command_status               （新增端點，見第二節）
+ get_family_context               （UC1.5，腳本早已存在）
```

`get_family_context` 跟 UC1.3 / UC1.4 是同一種情況：腳本在 `f4b12df Add
get_family_context API endpoint` 就 commit 了，但沒有掛進 `ROUTES`，所以
HTTP 一直打不到。程式碼沒有動，只補路由。

後六條要特別說明：**UC1.3 / UC1.4 的腳本已經寫好並 commit 了
（`dc60a77 feat: 完成 UC1.3 閘道器初始化與 UC1.4 跨場域信任功能`），
但沒有掛進 `ROUTES`，所以 HTTP 完全打不到**。程式碼本身沒有動，只是補上路由。

`provision_gateway_identity.py` **刻意沒有掛**：它用 argparse，是在 Gateway
本機執行的 CLI 佈建工具，不是 CGI 端點。

> 註：`UC.md` 目前仍把 UC1.3 / UC1.4 標記為 ❌，與 commit 訊息不一致，文件需要更新。

**變更 B — 加入 CORS middleware**

Flutter Web 版（`flutter run -d chrome`）從另一個 origin 發請求，沒有 CORS 標頭
會被瀏覽器直接擋掉。部分 CGI（例如 `list_devices.py`）自己有印
`Access-Control-Allow-Origin`，但 `run_cgi()` 只解析 `Status` 與 `Content-Type`
兩個標頭、其餘一律丟棄，所以那些設定從來沒有生效過。

目前設 `allow_origins=["*"]`、`allow_credentials=False`。這個 API 沒有 Cookie/Session
（身分靠 payload 的明文 `user_id`），所以不涉及 credentials 外洩。
**後端導入 Token 之後應改成明確的來源白名單。**

---

### 2. `API/list_devices/list_devices.py`（UC2.1）

**修正跨場域稽核日誌外洩。**

原本的 `logs` 查詢完全沒有 `family_id` 條件：

```sql
SELECT ... FROM audit_logs
WHERE action = 'DEVICE_REGISTERED'
ORDER BY timestamp DESC LIMIT 20
```

回傳的是**全系統**最近 20 筆裝置註冊紀錄（含 `device_id` 與雜湊鏈），
而 App 會把這份 logs 直接顯示在家庭詳情頁 —— 任何使用者都看得到別人家的裝置紀錄。
這違反 UC5.3「確保資料跨屋隔離」。

改成 `LEFT JOIN devices` 後依 `family_id` / `owner_user_id` 過濾。
`devices` 查詢的部分**完全沒動**。

相容性處理：早期寫入的 `audit_logs` 可能 `family_id` 為 NULL，
因此加上 `(a.family_id = %s OR (a.family_id IS NULL AND d.family_id = %s))`，
讓既有紀錄不會在修正後整批消失。

已回歸測試四種參數組合（無參數 / 只帶 owner / 只帶 family / GET 形式），行為正常。

---

## 二、新增的後端端點

前三支是 **App 從第一版就在呼叫、但後端根本沒有這支腳本** 的端點。
在補上之前，對應的畫面永遠是空的。

| 端點 | 檔案 | 解決的問題 |
| :-- | :-- | :-- |
| `POST/GET /get_user_families` | `API/get_user_families/get_user_families.py` | App 無法在不重新登入的情況下刷新場域清單（登入回應是快照，建立場域/接受邀請後就過期） |
| `POST/GET /get_family_members` | `API/get_family_members/get_family_members.py` | 「成員清單」分頁完全載不到資料，連帶 `update_member_role` 沒有入口 |
| `POST/GET /get_invitations` | `API/get_invitations/get_invitations.py` | 「邀請通知」永遠是空的 —— `send_invitation` 送得出去、受邀者卻看不到，`respond_invitation` 等於沒有入口 |
| `POST /create_family` | `API/create_family/create_family.py` | **原本沒有任何建立家庭的 API**（WHITEPAPER 第 11 節記載此缺口，測試靠手動 `INSERT INTO families`）。新使用者註冊完是死路，除非有人邀請 |
| `POST/GET /get_command_status` | `API/get_command_status/get_command_status.py` | **稽核鏈污染**。App 送出控制指令後要輪詢結果（MQTT 模式只回 `PUBLISHED`），原本拿 `/dashboard` 輪詢，而 `get_family_dashboard.py` 每次呼叫都寫一筆 `DASHBOARD_VIEWED` 進 `audit_logs` —— 每 2 秒一次、最多 15 次，於是**按一次解鎖會在雜湊鏈裡留下多達 15 筆假的「檢視儀表板」紀錄**。本專題的核心賣點是不可篡改的稽核鏈，鏈上絕大多數卻是 App 輪詢雜訊。這支只查一筆 `control_commands` + 該裝置最新一筆 `device_telemetry`，**不寫任何稽核日誌**，並檢查呼叫者是該場域的有效成員 |

> **既有 `audit_logs` 需要清理**：在改用 `get_command_status` 之前產生的
> `DASHBOARD_VIEWED` 雜訊已經寫進鏈裡了。雜湊鏈不能直接刪列（刪了後續
> `prev_hash` 全部對不上），Demo 前若要一條乾淨的鏈，建議重建測試資料庫
> 而不是手動 DELETE。

### 設計決策（請 review）

所有新端點都沿用既有慣例：CGI 形式、`{"payload": {...}}` 包裝、
`response_json()` 印 `Status:` header、`DictCursor`、UTF-8 reconfigure。

**權限模型**

- `get_family_members`：呼叫者必須是該場域的有效成員才能查（避免任意帳號列舉他人成員名單）；
  角色為 `Revoked` 視同無權限。**聯絡資訊（email / phone）只回給 Admin**。
- `get_invitations`：只回傳指定 `user_id` 自己的邀請；帳號不存在回 404
  （避免拿這支 API 探測任意 user_id 是否存在）。
- `get_user_families`：不回傳 `Revoked` 的關聯；額外算 `device_count` / `member_count` 給場域卡片用。
- `create_family`：同一使用者底下不允許同名場域（409）；單一使用者上限 20 個場域（純防呆）。

**沒有做的事**（刻意留給後端決定）

- `create_family` **沒有寫 `audit_logs`**。既有的雜湊鏈寫入邏輯是複製貼上散在各腳本裡
  （`device_pair.py` / `control_device.py` / `decommission_device.py` 各一份），
  我不想再複製第四份。建立場域是重要的安全事件，建議抽出共用模組後補上。
- 沒有為新端點寫 `README.md` / 規範 PDF（其他端點目錄下都有）。

---

## 三、新增的開發環境設定（不影響正式部署）

### `docker-compose.dev.yml` + `config/mosquitto.dev.conf`

**為什麼需要**：正式的 `config/mosquitto.conf` 是 TLS-only（8883），需要
`config/certs/server.key` 與 `ca.key`。這兩支私鑰依 `.gitignore` 不進版控，
所以 clone 下來的機器沒有，broker 起不來。

**為什麼不直接跑 `generate_certs.sh`**：那支腳本在 `ca.key` 不存在時會產生
**全新的 CA** 並覆蓋版控中的 `ca.crt`。而 `Arduino/MqttSmartLock/MqttSmartLock.ino`
內嵌了舊 CA 的 `MQTT_ROOT_CA` —— 換掉 `ca.crt` 會讓**已經燒錄好的實體 ESP32 連不上 broker**。

因此本機開發改走明文 1883，**完全沒有動到 `mosquitto.conf` 與 `config/certs/`**。

```bash
# 本機開發（明文 MQTT）
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d \
  mysql mqtt-broker api api-mqtt-bridge

# 正式 / 實機測試（TLS）— 照 README 原本的做法，不受影響
docker compose up -d
```

`docker-compose.dev.yml` 只覆蓋 `MQTT_PORT` / `MQTT_USE_TLS` / `OTA_HOST`
與 broker 的 `command`。`OTA_HOST` 目前寫的是開發機 IP `192.168.50.234`，
換機器要改這裡。

> 注意：`docker-compose.yml` 的 `version: '3.8'` 已被新版 Docker Compose 標為 obsolete，
> 每次執行都會印一行警告。刪掉那一行即可，我沒有動它。

---

## 四、App 端做了什麼（摘要）

不需要後端 review，列出來讓大家知道現況。完整內容見 `app/`。

**原本 App 對後端一支都打不通**，原因有三個疊在一起：

1. `ApiConfig` 組出 `http://localhost:8000/cgi-bin/<name>.py`，
   實際閘道是 `http://<IP>:8091/<name>` —— port、`/cgi-bin` 前綴、`.py` 副檔名三處都錯。
   而且它用 `Uri.base` 判斷平台，那個 API 只在 Web 有意義，手機上永遠落進 localhost 分支。
2. `android/app/src/main/AndroidManifest.xml` **沒有 `INTERNET` 權限**
   （只有 debug/profile 的 manifest 有，那是 Flutter 為 hot reload 自動加的）→ release 版完全沒有網路。
3. 後端是明文 HTTP，Android 9+ 與 iOS ATS 預設封鎖 → 需要 network security config 與 ATS 例外。

其餘主要工作：

- 新增遠端鎖控 UI（UC4.1）與場域儀表板（UC4.3）—— 這兩個核心功能原本完全沒有畫面
- 新增訪客 QR（UC3.4）、維修模式（UC5.1）、韌體更新（UC2.2）、建立場域、場域切換（UC1.5）
- 修掉數個會崩潰的 bug（空字串 `substring` 造成 RangeError、`int.parse` 無保護、
  6 處 `setState` 缺 `mounted` 檢查、5 處 `TextEditingController` 洩漏）
- 抽出 `ApiClient` 統一網路層、`AppTheme` 統一配色、清掉 Flutter 範本殘留

### 對 App 端影響最大的兩個後端行為

1. **`control_device` 在 `CONTROL_MODE=mqtt` 下只回 `PUBLISHED`**，不代表門真的開了。
   UI 因此做成「送出中 → 等待裝置回報 → 完成/逾時」三階段，並輪詢 `/dashboard` 確認。
   30 秒沒回報就明確告知使用者「指令已發送但裝置未回報」，不會謊報成功。
2. **`ota_update` 不檢查韌體檔案是否存在、也不比對版本**，打錯檔名一樣回成功。
   UI 因此在對話框裡明確警示，且成功後只顯示「已發送更新指令」，不顯示「更新成功」。

---

## 五、仍未解決 / 需要後端決定

依嚴重度排序：

1. **`/login` 不核發 Token**（UC1.2 只完成一半）。所有 API 靠 payload 裡的明文
   `user_id` 認身分 —— 知道某個 user_id 就等於可以用該身分呼叫任何 API。
   App 的「記住登入」因此只能保存 user_id（絕不保存密碼），
   其安全強度與後端現行模型一致，不多也不少。**這是目前最大的安全缺口。**
2. **`decommission_device.py` 檔頭自承「不含身分驗證版」** —— 任何呼叫者都能除役任意裝置。
   App 已把入口限制在 Admin，但那只是 UI 層，API 本身仍然全開。
3. **`generate_guest_qr` 的 `control_url` 是佔位網域** `https://your-domain.com/qr-control?...`。
   App 產生的 QR 會如實標示「掃描不會連到真實服務」，需要設定真實網域才能用。
4. **UC3.1 零信任 policy_rules 沒有任何寫入端點** —— 引擎在、管理介面不在，App 無法實作。
5. **UC3.5 撤銷訪客令牌**：`guest_tokens.revoked` 欄位存在但沒有端點會去設定它。
6. **UC1.3 / UC1.4 已可透過 HTTP 呼叫，但 App 還沒做對應畫面**。
   這兩組端點的請求格式較複雜（Gateway 本機 Identity、一次性 `GWINIT_` 碼、
   信任配對 token），需要跟負責人確認實際使用流程後再實作。
7. `UC.md` 的 UC1.3 / UC1.4 完成度標記已過期，與 `dc60a77` 的 commit 訊息矛盾。

---

## 六、如何驗證這些變更

後端（31 項檢查，涵蓋新端點的正常/邊界/權限情境）：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d \
  mysql mqtt-broker api api-mqtt-bridge
curl -s http://localhost:8091/healthz          # {"status":"ok"}
curl -s http://localhost:8091/openapi.json | python -c "import json,sys; print(len(json.load(sys.stdin)['paths']), 'routes')"
# 25 routes（原本 15）
```

App 端：

```bash
cd app
flutter analyze                                 # No issues found!
flutter test test/widget_test.dart              # 29 passed（解析工具的單元測試）
# 整合測試會真的打後端，預設 skip，需明確開啟：
flutter test test/api_integration_test.dart --dart-define=RUN_API_TESTS=true
# 18 passed —— 涵蓋 register/login/create_family/邀請全鏈路/配對/
#              control_device 的 PUBLISHED 語意/維修模式擋控制/
#              跨場域日誌隔離/錯誤分類
```

整合測試裡有兩項是專門用來**證明修復有效**的對照測試：

- `list_devices 只帶 family_id 時成員也看得到裝置` —— 同時驗證舊寫法（帶 `user_id`）會回空清單
- `list_devices 的稽核日誌不含其他場域的紀錄`

---

## 七、環境需求變更

App 加入 `shared_preferences` / `qr_flutter` 後，解析出來的相依集合需要
**Dart 3.12 / Flutter 3.44 以上**（見 `app/pubspec.lock` 的 `sdks` 區段）。
`app/pubspec.yaml` 的 `environment` 已如實宣告，版本不符會得到明確錯誤訊息。
本次開發使用 Flutter 3.44.8 / Dart 3.12.2。

---

有任何一項變更你們不同意，直接改掉或找我討論都可以 —— 動到你們的檔案是為了讓
App 能跑起來驗證，不是要接管那些模組。
