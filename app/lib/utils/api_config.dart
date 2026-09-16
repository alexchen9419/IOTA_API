import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 後端 API 位址設定。
///
/// 舊版做法是用 `Uri.base` 判斷要打 `http://localhost:8000/cgi-bin/<name>.py`，
/// 有三個問題導致 App 對後端「一支都打不通」：
///
///   1. `Uri.base` 只在 Flutter Web 有意義。Android / iOS / Windows 上它是
///      `file:///`，host 為空字串，於是永遠落進 localhost 分支 —— 而手機上的
///      localhost 指向手機自己，不是開發機。
///   2. 實際的閘道是 FastAPI（[API/gateway.py]），跑在 `:8091`，不是 `:8000`。
///   3. 路由是 `/<endpoint>`，沒有 `/cgi-bin` 前綴、也沒有 `.py` 副檔名。
///
/// 現在的解析順序（先找到的優先）：
///   1. 使用者在 App 內「伺服器設定」存下來的位址（SharedPreferences）
///   2. 編譯時傳入的 `--dart-define=API_BASE_URL=http://x.x.x.x:8091`
///   3. 依平台推測的預設值（見 [_platformDefaultBaseUrl]）
///
/// 之所以要能在 App 內改：後端跑在開發機的區網 IP 上（跟 Arduino sketch 的
/// `MQTT_BROKER`、docker-compose 的 `OTA_HOST` 同一台），這個 IP 會隨著換
/// 機器 / 換網路而變，寫死在程式裡每次都要重編譯。
class ApiConfig {
  ApiConfig._();

  static const String _prefsKey = 'api_base_url';

  /// `flutter run --dart-define=API_BASE_URL=http://192.168.1.5:8091`
  static const String _compileTimeBaseUrl =
      String.fromEnvironment('API_BASE_URL');

  /// 後端閘道對外的埠（docker-compose.yml 把容器 8000 映射到主機 8091）。
  static const int defaultPort = 8091;

  /// Android 模擬器用 10.0.2.2 走到宿主機的 localhost；實體手機不適用。
  static const String _androidEmulatorHost = '10.0.2.2';

  static String? _runtimeBaseUrl;
  static SharedPreferences? _prefs;

  /// 在 `runApp()` 之前呼叫一次，把使用者存過的位址讀回來。
  static Future<void> init() async {
    _prefs = await SharedPreferences.getInstance();
    final saved = _prefs?.getString(_prefsKey);
    if (saved != null && saved.trim().isNotEmpty) {
      _runtimeBaseUrl = _normalize(saved);
    }
  }

  /// 目前生效的 base URL，結尾不含斜線。
  static String get baseUrl {
    final runtime = _runtimeBaseUrl;
    if (runtime != null && runtime.isNotEmpty) return runtime;
    if (_compileTimeBaseUrl.isNotEmpty) return _normalize(_compileTimeBaseUrl);
    return _platformDefaultBaseUrl;
  }

  /// 使用者是否曾經手動設定過（設定頁用來顯示「目前使用預設值」）。
  static bool get hasUserOverride =>
      _runtimeBaseUrl != null && _runtimeBaseUrl!.isNotEmpty;

  /// 這組預設值的來源說明，顯示在設定頁讓使用者知道現在是怎麼來的。
  static String get sourceDescription {
    if (hasUserOverride) return '手動設定';
    if (_compileTimeBaseUrl.isNotEmpty) return '編譯時 --dart-define';
    return '平台預設值';
  }

  static Future<void> setBaseUrl(String url) async {
    final normalized = _normalize(url);
    _runtimeBaseUrl = normalized;
    _prefs ??= await SharedPreferences.getInstance();
    await _prefs?.setString(_prefsKey, normalized);
  }

  static Future<void> clearOverride() async {
    _runtimeBaseUrl = null;
    _prefs ??= await SharedPreferences.getInstance();
    await _prefs?.remove(_prefsKey);
  }

  /// 組出端點完整位址。請一律傳 [ApiEndpoints] 的常數，不要傳字面字串 ——
  /// 之前 `get_user_families.py` / `get_invitations.py` / `get_family_members.py`
  /// 三支「後端根本不存在」的端點就是這樣打錯進去的。
  static Uri uri(String endpoint) =>
      Uri.parse('$baseUrl/${endpoint.replaceAll(RegExp(r'^/+'), '')}');

  static String get _platformDefaultBaseUrl {
    // Web：跟著網頁本身的 origin 走，開發時通常有 proxy 或同源部署。
    if (kIsWeb) {
      final base = Uri.base;
      if (base.hasAuthority) {
        return '${base.scheme}://${base.host}:$defaultPort';
      }
      return 'http://localhost:$defaultPort';
    }

    // Android 模擬器連宿主機要用 10.0.2.2。實體裝置請在設定頁改成開發機的區網 IP。
    if (defaultTargetPlatform == TargetPlatform.android) {
      return 'http://$_androidEmulatorHost:$defaultPort';
    }

    // 桌面 / iOS 模擬器跟後端同一台機器時 localhost 就是對的。
    return 'http://localhost:$defaultPort';
  }

  /// 補上 scheme、去掉結尾斜線；使用者常常只輸入 `192.168.1.5:8091`。
  static String _normalize(String raw) {
    var url = raw.trim();
    if (url.isEmpty) return url;
    if (!url.contains('://')) url = 'http://$url';
    while (url.endsWith('/')) {
      url = url.substring(0, url.length - 1);
    }
    return url;
  }
}

/// 後端閘道的路由名稱。
///
/// 對應 [API/gateway.py] 的 `ROUTES`。集中成常數是為了避免再出現「App 呼叫的
/// 端點後端根本沒有」這種只有在執行期才會發現的錯誤。
class ApiEndpoints {
  ApiEndpoints._();

  // 帳號與場域
  static const String register = 'register';
  static const String login = 'login';
  static const String createFamily = 'create_family';
  static const String getUserFamilies = 'get_user_families';
  static const String getFamilyMembers = 'get_family_members';

  // 邀請與權限
  static const String getInvitations = 'get_invitations';
  static const String sendInvitation = 'send_invitation';
  static const String respondInvitation = 'respond_invitation';
  static const String updateMemberRole = 'update_member_role';
  static const String generateGuestQr = 'generate_guest_qr';

  // 裝置生命週期
  static const String devicePair = 'device_pair';
  static const String listDevices = 'list_devices';
  static const String decommissionDevice = 'decommission_device';
  static const String otaUpdate = 'ota_update';
  static const String maintenanceMode = 'maintenance_mode';

  // 控制與監控
  static const String controlDevice = 'control_device';
  static const String dashboard = 'dashboard';

  /// 控制指令送出後的輪詢專用端點。**不要**改回用 [dashboard] 輪詢：
  /// get_family_dashboard.py 每次呼叫都會寫一筆 DASHBOARD_VIEWED 進
  /// audit_logs，輪詢會把雜湊鏈灌滿雜訊。
  static const String getCommandStatus = 'get_command_status';

  // 健康檢查（設定頁的「測試連線」用）
  static const String healthz = 'healthz';
}
