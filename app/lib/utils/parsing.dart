/// 後端回應的防禦性解析工具。
///
/// CGI 端用 `json.dumps(..., default=str)` 輸出，datetime 會變成字串、
/// 數字欄位在不同腳本間也可能是 int 或字串。原本畫面端直接用
/// `int.parse(x.toString())`，只要後端回了預期外的值就整頁崩潰。
library;

/// 寬鬆轉整數。無法轉換時回 `null` 而不是丟例外。
int? asInt(dynamic value) {
  if (value == null) return null;
  if (value is int) return value;
  if (value is double) return value.toInt();
  if (value is bool) return value ? 1 : 0;
  return int.tryParse(value.toString().trim());
}

/// 寬鬆轉布林。後端的 TINYINT(1) 可能回 0/1、true/false 或 "0"/"1"。
bool asBool(dynamic value, {bool defaultValue = false}) {
  if (value == null) return defaultValue;
  if (value is bool) return value;
  if (value is num) return value != 0;
  final text = value.toString().trim().toLowerCase();
  if (text.isEmpty) return defaultValue;
  return text == '1' || text == 'true' || text == 'yes';
}

/// 寬鬆轉字串，`null` 與 "null" 都回退成 [fallback]。
String asText(dynamic value, {String fallback = '—'}) {
  if (value == null) return fallback;
  final text = value.toString().trim();
  if (text.isEmpty || text == 'null') return fallback;
  return text;
}

/// 解析後端回傳的時間字串（`YYYY-MM-DD HH:MM:SS`）。
DateTime? asDateTime(dynamic value) {
  if (value == null) return null;
  if (value is DateTime) return value;
  return DateTime.tryParse(value.toString().trim().replaceFirst(' ', 'T'));
}

/// 取名字的第一個字當頭像文字。
///
/// 舊版寫 `username.toString().substring(0, 1)`：`username` 是空字串時
/// `substring` 會丟 RangeError，讓整個主畫面白屏。
String initialOf(String? name, {String fallback = '?'}) {
  final trimmed = (name ?? '').trim();
  if (trimmed.isEmpty) return fallback;
  // characters 套件才能正確處理 emoji / 組合字，但這裡取單一字元夠用，
  // 且中文名字每個 code unit 就是一個字。
  return trimmed.substring(0, 1).toUpperCase();
}

/// 角色權限判斷的單一來源。
///
/// 原本三個畫面各寫一份：`family_detail` 的 `_isAdmin` 與 `family_list` 的
/// `isAdmin` 只認 `admin`，但 `_canControl` 與 [roleLabel] 都把 `owner` 當
/// 管理員。結果 `owner` 在畫面上顯示「管理員」、也能控制裝置，卻看不到管理
/// 選單、發送邀請分頁與訪客 QR —— 同一個角色在不同畫面有不同權限。
///
/// 兩個判斷都集中在這裡，未來後端新增角色只要改這一處。
bool isFamilyAdmin(dynamic role) {
  final text = asText(role, fallback: '').toLowerCase();
  return text == 'admin' || text == 'owner';
}

/// 能否下發控制指令。對應 `control_device.py` 的角色檢查（Guest 會被擋掉）。
bool canControlDevices(dynamic role) {
  final text = asText(role, fallback: '').toLowerCase();
  return text == 'admin' || text == 'owner' || text == 'member';
}

/// 角色代碼 → 顯示名稱。
///
/// 後端的角色是 `Admin` / `Member` / `Guest` / `Technician` / `SP` / `Revoked`，
/// 原本直接把英文代碼顯示給使用者看。
String roleLabel(dynamic role) {
  switch (asText(role, fallback: '').toLowerCase()) {
    case 'admin':
    case 'owner':
      return '管理員';
    case 'member':
      return '家庭成員';
    case 'guest':
      return '訪客';
    case 'technician':
      return '維修人員';
    case 'sp':
      return '服務供應商';
    case 'revoked':
      return '已撤銷';
    default:
      return asText(role, fallback: '未知');
  }
}

/// 裝置狀態代碼 → 顯示名稱。
String deviceStatusLabel(dynamic status) {
  switch (asText(status, fallback: '').toLowerCase()) {
    case 'active':
      return '運作中';
    case 'revoked':
    case 'retired':
    case 'decommissioned':
      return '已除役';
    case 'inactive':
      return '停用';
    default:
      return asText(status, fallback: '未知');
  }
}

/// 實體狀態代碼 → 顯示名稱（鎖具）。
String physicalStateLabel(dynamic state) {
  switch (asText(state, fallback: '').toLowerCase()) {
    case 'locked':
      return '已上鎖';
    case 'unlocked':
      return '已解鎖';
    case 'open':
      return '開啟';
    case 'closed':
      return '關閉';
    case 'on':
      return '開';
    case 'off':
      return '關';
    default:
      return asText(state, fallback: '未知');
  }
}

/// 把後端的時間字串轉成「幾分鐘前」這種相對描述。
String relativeTime(dynamic value, {String fallback = '從未回報'}) {
  final dt = asDateTime(value);
  if (dt == null) return fallback;

  final diff = DateTime.now().difference(dt);
  if (diff.isNegative) return '剛剛';
  if (diff.inSeconds < 60) return '${diff.inSeconds} 秒前';
  if (diff.inMinutes < 60) return '${diff.inMinutes} 分鐘前';
  if (diff.inHours < 24) return '${diff.inHours} 小時前';
  if (diff.inDays < 30) return '${diff.inDays} 天前';
  return '${dt.year}-${_two(dt.month)}-${_two(dt.day)}';
}

/// 格式化成後端接受的 `YYYY-MM-DD HH:MM:SS`。
String formatForApi(DateTime dt) =>
    '${dt.year}-${_two(dt.month)}-${_two(dt.day)} '
    '${_two(dt.hour)}:${_two(dt.minute)}:00';

/// 顯示用的時間格式。
String formatDisplay(dynamic value, {String fallback = '未設定'}) {
  final dt = asDateTime(value);
  if (dt == null) return fallback;
  return '${dt.year}-${_two(dt.month)}-${_two(dt.day)} '
      '${_two(dt.hour)}:${_two(dt.minute)}';
}

String _two(int n) => n.toString().padLeft(2, '0');
