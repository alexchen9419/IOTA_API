import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

/// 登入狀態的本機保存。
///
/// ⚠️ 安全性說明（必讀）
///
/// 後端的 `/login` **不核發任何 Token**（UC1.2 只完成一半）：驗證密碼之後就
/// 回傳使用者資料，後續每一支 API 都是靠 payload 裡的明文 `user_id` 判斷身分。
/// 也就是說，「知道某個 user_id」等同於「可以用那個身分呼叫任何 API」。
///
/// 在這個前提下：
///   * 這裡只保存 user_id / username / status，**絕不保存密碼**。
///     保存密碼才能做真正的重新驗證，但那會讓風險比現在更高。
///   * 因此這不是真正的「session 續期」，而是「記住上次登入的身分」，
///     其安全強度與後端現行的身分模型完全一致 —— 不多也不少。
///
/// 後端導入 Token 之後，這裡應該改成保存 access token 與 refresh token，
/// 並在啟動時向後端驗證有效性，而不是無條件信任本機資料。
class Session {
  Session._();

  static const String _key = 'session_user';

  static Map<String, dynamic>? _current;

  /// 目前登入者的資料（等同 `/login` 回應的 `data`）。
  static Map<String, dynamic>? get current => _current;

  static bool get isLoggedIn => _current != null;

  static String get userId => (_current?['user_id'] ?? '').toString();

  static String get username => (_current?['username'] ?? '').toString();

  /// 在 `runApp()` 之前呼叫，把上次登入的身分讀回來。
  static Future<void> restore() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_key);
    if (raw == null || raw.isEmpty) return;

    try {
      final decoded = json.decode(raw);
      if (decoded is Map<String, dynamic> && decoded['user_id'] != null) {
        _current = decoded;
      }
    } on FormatException {
      // 舊版格式或資料損毀就當作沒登入，並清掉避免每次啟動都失敗。
      await prefs.remove(_key);
    }
  }

  static Future<void> save(Map<String, dynamic> userData) async {
    // families 是登入當下的快照，會過期；不保存它，改由 get_user_families
    // 在進入畫面時重新查，避免顯示已經被移除權限的場域。
    final toStore = Map<String, dynamic>.from(userData)..remove('families');

    _current = toStore;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_key, json.encode(toStore));
  }

  static Future<void> clear() async {
    _current = null;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_key);
  }
}
