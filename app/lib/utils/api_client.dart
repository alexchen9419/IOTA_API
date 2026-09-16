import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import 'api_config.dart';

/// 呼叫失敗的分類。UI 靠這個決定要不要提供「重試」或「去設定伺服器位址」。
enum ApiErrorKind {
  /// 沒有錯誤。
  none,

  /// 連不上主機、逾時、DNS 失敗 —— 通常是伺服器沒開或位址設錯。
  network,

  /// 後端回了非 2xx，或 body 裡 status 是 Error（業務邏輯錯誤，訊息可直接顯示）。
  business,

  /// 權限不足（401/403）。
  forbidden,

  /// 後端回了看不懂的東西（HTML 錯誤頁、空回應、壞掉的 JSON）。
  malformed,
}

/// 統一的 API 回應。
///
/// 後端每支 CGI 都回 `{"status": "Success|Warning|Error", "msg": ..., "data": ...}`
/// 並用 `Status:` header 決定 HTTP 狀態碼，這裡把兩者合併成一個結果物件，
/// 讓畫面端不用每次都重寫一次 `statusCode == 200 && body['status'] == 'Success'`。
class ApiResult {
  const ApiResult({
    required this.ok,
    required this.statusCode,
    required this.message,
    required this.kind,
    this.data,
    this.raw,
  });

  final bool ok;
  final int statusCode;
  final String message;
  final ApiErrorKind kind;

  /// 後端 `data` 欄位。可能是 Map 或 List，呼叫端自己判斷。
  final dynamic data;

  /// 完整的回應 body，少數需要讀 `meta` 之類欄位時用。
  final Map<String, dynamic>? raw;

  /// `data` 當成物件取用；型別不符時回空 Map，避免呼叫端到處寫型別判斷。
  Map<String, dynamic> get dataMap =>
      data is Map<String, dynamic> ? data as Map<String, dynamic> : const {};

  /// `data` 當成陣列取用；型別不符時回空陣列。
  List<dynamic> get dataList => data is List ? data as List<dynamic> : const [];

  bool get isNetworkError => kind == ApiErrorKind.network;
}

/// 後端 API 呼叫的單一入口。
///
/// 集中處理逾時、連線錯誤轉譯、UTF-8 解碼與 `status` 語意，取代原本散在四個
/// 畫面裡、各自複製一份的 `http.post` + `json.decode` + try/catch。
class ApiClient {
  ApiClient._();

  static const Duration timeout = Duration(seconds: 15);

  /// 控制指令走 MQTT 發布，後端可能要等 broker 回應，給長一點。
  static const Duration controlTimeout = Duration(seconds: 25);

  static Future<ApiResult> post(
    String endpoint,
    Map<String, dynamic> payload, {
    Duration? timeoutOverride,
  }) {
    return _send(
      endpoint,
      method: 'POST',
      payload: payload,
      timeoutOverride: timeoutOverride,
    );
  }

  static Future<ApiResult> get(
    String endpoint, {
    Map<String, String>? query,
    Duration? timeoutOverride,
  }) {
    return _send(
      endpoint,
      method: 'GET',
      query: query,
      timeoutOverride: timeoutOverride,
    );
  }

  /// 設定頁的「測試連線」。只打 `/healthz`，不需要帳號。
  static Future<ApiResult> ping({String? baseUrlOverride}) async {
    final base = baseUrlOverride ?? ApiConfig.baseUrl;
    final uri = Uri.parse('$base/${ApiEndpoints.healthz}');
    try {
      final response =
          await http.get(uri).timeout(const Duration(seconds: 8));
      if (response.statusCode == 200) {
        return const ApiResult(
          ok: true,
          statusCode: 200,
          message: '連線成功',
          kind: ApiErrorKind.none,
        );
      }
      return ApiResult(
        ok: false,
        statusCode: response.statusCode,
        message: '伺服器回應 ${response.statusCode}，請確認位址是否指向 API 閘道',
        kind: ApiErrorKind.malformed,
      );
    } catch (e) {
      return ApiResult(
        ok: false,
        statusCode: 0,
        message: _networkMessage(e, uri),
        kind: ApiErrorKind.network,
      );
    }
  }

  static Future<ApiResult> _send(
    String endpoint, {
    required String method,
    Map<String, dynamic>? payload,
    Map<String, String>? query,
    Duration? timeoutOverride,
  }) async {
    var uri = ApiConfig.uri(endpoint);
    if (query != null && query.isNotEmpty) {
      uri = uri.replace(queryParameters: query);
    }

    try {
      final http.Response response;
      if (method == 'GET') {
        response = await http.get(uri).timeout(timeoutOverride ?? timeout);
      } else {
        response = await http
            .post(
              uri,
              headers: const {
                'Content-Type': 'application/json; charset=utf-8',
              },
              // 後端每支 CGI 都是讀 {"payload": {...}} 這層包裝。
              body: json.encode({'payload': payload ?? const {}}),
            )
            .timeout(timeoutOverride ?? timeout);
      }

      return _parse(response, uri);
    } on TimeoutException {
      return ApiResult(
        ok: false,
        statusCode: 0,
        message: '連線逾時（${(timeoutOverride ?? timeout).inSeconds} 秒）。'
            '請確認後端服務正在執行，以及伺服器位址設定正確。',
        kind: ApiErrorKind.network,
      );
    } catch (e) {
      return ApiResult(
        ok: false,
        statusCode: 0,
        message: _networkMessage(e, uri),
        kind: ApiErrorKind.network,
      );
    }
  }

  static ApiResult _parse(http.Response response, Uri uri) {
    final status = response.statusCode;

    // 後端一律回 JSON。收到 HTML 多半是打到錯的服務（例如 Node-RED 的 1880）。
    Map<String, dynamic>? body;
    try {
      final decoded = json.decode(utf8.decode(response.bodyBytes));
      if (decoded is Map<String, dynamic>) body = decoded;
    } on FormatException {
      body = null;
    }

    if (body == null) {
      return ApiResult(
        ok: false,
        statusCode: status,
        message: status == 404
            ? '找不到端點 ${uri.path}，請確認後端版本是否為最新'
            : '伺服器回應格式不正確（HTTP $status）',
        kind: ApiErrorKind.malformed,
      );
    }

    final bodyStatus = (body['status'] ?? '').toString();
    // 後端訊息欄位不統一：多數 CGI 用 `msg`，但 control_device.py 與
    // get_family_dashboard.py 用 `message`。兩個都收，避免訊息變成空字串。
    final message = (body['msg'] ?? body['message'] ?? '').toString();

    // 有些端點成功時回 200，有些回 201（send_invitation、create_family）。
    // update_member_role 的 no-op 會回 Warning，那也算成功。
    final httpOk = status >= 200 && status < 300;
    final statusOk = bodyStatus == 'Success' || bodyStatus == 'Warning';

    if (httpOk && statusOk) {
      return ApiResult(
        ok: true,
        statusCode: status,
        message: message.isEmpty ? '操作成功' : message,
        kind: ApiErrorKind.none,
        data: body['data'],
        raw: body,
      );
    }

    return ApiResult(
      ok: false,
      statusCode: status,
      message: message.isEmpty ? '操作失敗（HTTP $status）' : message,
      kind: (status == 401 || status == 403)
          ? ApiErrorKind.forbidden
          : ApiErrorKind.business,
      data: body['data'],
      raw: body,
    );
  }

  /// 把底層例外翻成使用者看得懂的話。
  ///
  /// 原本各畫面直接把 `$e` 塞進 SnackBar，會把內部位址與堆疊細節顯示給使用者，
  /// 而且對「到底該怎麼辦」毫無幫助。
  static String _networkMessage(Object error, Uri uri) {
    final target = '${uri.scheme}://${uri.authority}';

    if (error is SocketException) {
      return '無法連線到 $target。請確認後端服務已啟動、手機與伺服器在同一個網路，'
          '並在設定頁確認伺服器位址。';
    }
    if (error is HandshakeException) {
      return '與 $target 的安全連線建立失敗，請確認是否誤用了 https。';
    }
    if (error is http.ClientException) {
      return '連線 $target 時中斷，請重試或確認伺服器位址。';
    }

    // 沒預期到的例外：畫面只顯示通用訊息，細節留給 debug console。
    if (kDebugMode) {
      debugPrint('[ApiClient] 未預期的錯誤 ($target): $error');
    }
    return '連線 $target 失敗，請稍後再試。';
  }
}
