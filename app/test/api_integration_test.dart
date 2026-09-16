// 對「實際執行中的後端」進行的整合測試。
//
// 這些測試會真的發 HTTP 請求，因此預設是 skip 的 —— CI 或沒開後端的機器上
// 不該因為連不到伺服器而失敗。
//
// 執行方式（先把後端跑起來）：
//   cd "IOTA APP"
//   docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
//   cd app
//   flutter test test/api_integration_test.dart --dart-define=RUN_API_TESTS=true
//
// 需要不同位址時再加 --dart-define=API_BASE_URL=http://192.168.x.x:8091
//
// 驗證重點是 App 的網路層（ApiClient / ApiConfig / ApiResult 解析）能不能
// 正確處理後端各支端點的真實回應，包含後端訊息欄位 msg / message 不一致、
// 201 與 Warning 也算成功、以及錯誤狀態碼的分類。

import 'package:flutter_test/flutter_test.dart';
import 'package:my_app/utils/api_client.dart';
import 'package:my_app/utils/api_config.dart';
import 'package:shared_preferences/shared_preferences.dart';

const bool _runApiTests =
    bool.fromEnvironment('RUN_API_TESTS', defaultValue: false);

/// 每次執行用不同後綴，避免第二次跑就撞到「帳號已存在」。
final String _suffix =
    DateTime.now().millisecondsSinceEpoch.toString().substring(7);

void main() {
  final String adminId = 'itadmin$_suffix';
  final String memberId = 'itmember$_suffix';
  const String password = 'Test1234!';

  int? familyId;

  setUpAll(() async {
    // ApiConfig 會讀 SharedPreferences；在純 VM 測試環境要先給它假的實作。
    SharedPreferences.setMockInitialValues({});
    await ApiConfig.init();

    const fromDefine = String.fromEnvironment('API_BASE_URL');
    await ApiConfig.setBaseUrl(
      fromDefine.isNotEmpty ? fromDefine : 'http://localhost:8091',
    );
  });

  group('後端連通性', () {
    test('healthz 有回應', () async {
      final result = await ApiClient.ping();
      expect(result.ok, isTrue, reason: '後端沒開？位址：${ApiConfig.baseUrl}');
    }, skip: !_runApiTests);
  });

  group('帳號與場域', () {
    // 後端各端點的成功狀態碼並不一致：register/login 回 200，
    // send_invitation/create_family 回 201。ApiClient 兩者都當成功處理。
    test('register 成功', () async {
      final result = await ApiClient.post(ApiEndpoints.register, {
        'user_id': adminId,
        'username': '整合測試管理員',
        'password': password,
        'email': '$adminId@example.com',
        'phone_number': '0912345678',
      });
      expect(result.ok, isTrue, reason: result.message);
      expect(result.statusCode, anyOf(200, 201));
    }, skip: !_runApiTests);

    test('重複註冊會被歸類為 business error 而非 network error', () async {
      final result = await ApiClient.post(ApiEndpoints.register, {
        'user_id': adminId,
        'username': '重複',
        'password': password,
        'email': 'dup@example.com',
        'phone_number': '0900000000',
      });
      expect(result.ok, isFalse);
      expect(result.kind, ApiErrorKind.business);
      expect(result.message, isNotEmpty);
    }, skip: !_runApiTests);

    test('login 成功並回傳 families 陣列', () async {
      final result = await ApiClient.post(ApiEndpoints.login, {
        'user_id': adminId,
        'password': password,
      });
      expect(result.ok, isTrue, reason: result.message);
      expect(result.dataMap['user_id'], adminId);
      expect(result.dataMap['families'], isA<List>());
    }, skip: !_runApiTests);

    test('密碼錯誤回 401 並歸類為 forbidden', () async {
      final result = await ApiClient.post(ApiEndpoints.login, {
        'user_id': adminId,
        'password': 'wrong-password',
      });
      expect(result.ok, isFalse);
      expect(result.statusCode, 401);
      expect(result.kind, ApiErrorKind.forbidden);
    }, skip: !_runApiTests);

    test('create_family 建立場域（原本後端沒有這支端點）', () async {
      final result = await ApiClient.post(ApiEndpoints.createFamily, {
        'user_id': adminId,
        'family_name': '整合測試場域 $_suffix',
      });
      expect(result.ok, isTrue, reason: result.message);
      familyId = result.dataMap['family_id'] as int?;
      expect(familyId, isNotNull);
      expect(result.dataMap['user_role'], 'Admin');
    }, skip: !_runApiTests);

    test('get_user_families 可在不重新登入的情況下刷新清單', () async {
      final result = await ApiClient.post(ApiEndpoints.getUserFamilies, {
        'user_id': adminId,
      });
      expect(result.ok, isTrue, reason: result.message);
      expect(result.dataList, isNotEmpty);
      final ids = result.dataList.map((f) => f['family_id']).toList();
      expect(ids, contains(familyId));
    }, skip: !_runApiTests);

    test('get_family_members 回傳成員與 is_self 標記', () async {
      final result = await ApiClient.post(ApiEndpoints.getFamilyMembers, {
        'family_id': familyId,
        'user_id': adminId,
      });
      expect(result.ok, isTrue, reason: result.message);
      final members = result.dataMap['members'] as List;
      expect(members, hasLength(1));
      expect(members.first['is_self'], isTrue);
      expect(members.first['role'], 'Admin');
    }, skip: !_runApiTests);
  });

  group('邀請流程', () {
    test('註冊第二個帳號', () async {
      final result = await ApiClient.post(ApiEndpoints.register, {
        'user_id': memberId,
        'username': '整合測試成員',
        'password': password,
        'email': '$memberId@example.com',
        'phone_number': '0922222222',
      });
      expect(result.ok, isTrue, reason: result.message);
    }, skip: !_runApiTests);

    test('send_invitation → get_invitations → respond_invitation 全鏈路', () async {
      final send = await ApiClient.post(ApiEndpoints.sendInvitation, {
        'family_id': familyId,
        'admin_uid': adminId,
        'invitee_uid': memberId,
        'role': 'Member',
      });
      expect(send.ok, isTrue, reason: send.message);

      // get_invitations 原本後端不存在，邀請通知分頁永遠是空的。
      final list = await ApiClient.post(ApiEndpoints.getInvitations, {
        'user_id': memberId,
      });
      expect(list.ok, isTrue, reason: list.message);
      expect(list.dataList, hasLength(1));

      final invitation = list.dataList.first as Map<String, dynamic>;
      expect(invitation['family_name'], isNotNull);
      expect(invitation['inviter_name'], isNotNull);

      final respond = await ApiClient.post(ApiEndpoints.respondInvitation, {
        'invitation_id': invitation['invitation_id'],
        'user_id': memberId,
        'action': 'Accept',
      });
      expect(respond.ok, isTrue, reason: respond.message);

      final after = await ApiClient.post(ApiEndpoints.getInvitations, {
        'user_id': memberId,
      });
      expect(after.dataList, isEmpty, reason: '接受後不該還在待處理清單');
    }, skip: !_runApiTests);
  });

  group('裝置與控制', () {
    final deviceId = 'IT:00:00:00:00:$_suffix'.substring(0, 17);

    test('device_pair 配對裝置', () async {
      final result = await ApiClient.post(ApiEndpoints.devicePair, {
        'owner_user_id': adminId,
        'family_id': familyId,
        'device_id': deviceId,
        'device_name': '整合測試門鎖',
        'device_type': 'smart_lock',
      });
      expect(result.ok, isTrue, reason: result.message);
      expect(result.dataMap['session_key_hash'], isNotNull);
    }, skip: !_runApiTests);

    test('list_devices 只帶 family_id 時成員也看得到裝置', () async {
      // 這是舊 App 的 bug：同時送 user_id 會被當成 owner_user_id 過濾，
      // 導致非配對者（一般成員）看到空清單。
      final asOwner = await ApiClient.post(ApiEndpoints.listDevices, {
        'family_id': familyId,
      });
      expect(asOwner.ok, isTrue, reason: asOwner.message);
      final devices = asOwner.dataMap['devices'] as List;
      expect(devices, hasLength(1));

      // 對照組：帶了 user_id（成員不是 owner）就會變成空清單。
      final withUserId = await ApiClient.post(ApiEndpoints.listDevices, {
        'family_id': familyId,
        'user_id': memberId,
      });
      expect((withUserId.dataMap['devices'] as List), isEmpty,
          reason: '證實舊寫法會讓成員看不到任何裝置');
    }, skip: !_runApiTests);

    test('list_devices 的稽核日誌不含其他場域的紀錄', () async {
      final result = await ApiClient.post(ApiEndpoints.listDevices, {
        'family_id': familyId,
      });
      final logs = result.dataMap['logs'] as List;
      for (final log in logs) {
        expect(log['device_id'], deviceId,
            reason: '出現了不屬於此場域的裝置紀錄（跨場域外洩）');
      }
    }, skip: !_runApiTests);

    test('control_device 回 PUBLISHED 而非「已完成」', () async {
      final result = await ApiClient.post(
        ApiEndpoints.controlDevice,
        {
          'family_id': familyId,
          'device_id': deviceId,
          'action': 'UNLOCK',
          'auth_type': 'user',
          'user_id': adminId,
          'parameters': const <String, dynamic>{},
        },
        timeoutOverride: ApiClient.controlTimeout,
      );
      expect(result.ok, isTrue, reason: result.message);
      // ApiClient 必須讀得到 `message` 欄位（這支端點不是用 `msg`）。
      expect(result.message, isNotEmpty);
      expect(result.dataMap['command_status'], 'PUBLISHED');
      expect(result.dataMap['command_id'], isNotNull);
    }, skip: !_runApiTests);

    test('dashboard 回傳 summary 與裝置健康度', () async {
      final result = await ApiClient.post(ApiEndpoints.dashboard, {
        'auth_type': 'user',
        'user_id': adminId,
        'family_id': familyId,
        'include_history': true,
        'history_limit': 3,
      });
      expect(result.ok, isTrue, reason: result.message);
      expect(result.dataMap['summary'], isA<Map>());

      final devices = result.dataMap['devices'] as List;
      expect(devices, hasLength(1));
      expect(devices.first['connection_health'], isNotNull);
      expect(devices.first['last_command'], isA<Map>());
    }, skip: !_runApiTests);

    test('維修模式開啟後控制指令會被拒絕', () async {
      final enable = await ApiClient.post(ApiEndpoints.maintenanceMode, {
        'family_id': familyId,
        'admin_uid': adminId,
        'device_id': deviceId,
        'action': 'Enable',
        'duration_minutes': 5,
        'reason': '整合測試',
      });
      expect(enable.ok, isTrue, reason: enable.message);

      final blocked = await ApiClient.post(
        ApiEndpoints.controlDevice,
        {
          'family_id': familyId,
          'device_id': deviceId,
          'action': 'LOCK',
          'auth_type': 'user',
          'user_id': adminId,
        },
        timeoutOverride: ApiClient.controlTimeout,
      );
      expect(blocked.ok, isFalse, reason: '維修模式中不該放行控制指令');
      expect(blocked.statusCode, 409);

      final disable = await ApiClient.post(ApiEndpoints.maintenanceMode, {
        'family_id': familyId,
        'admin_uid': adminId,
        'device_id': deviceId,
        'action': 'Disable',
      });
      expect(disable.ok, isTrue, reason: disable.message);
    }, skip: !_runApiTests);

    test('decommission_device 除役', () async {
      final result = await ApiClient.post(ApiEndpoints.decommissionDevice, {
        'device_id': deviceId,
        'operator_user_id': adminId,
        'reason': '整合測試結束',
      });
      expect(result.ok, isTrue, reason: result.message);
    }, skip: !_runApiTests);
  });

  group('錯誤處理', () {
    test('連不到主機時歸類為 network error 且訊息不含堆疊細節', () async {
      // 127.0.0.1:9 是 discard port，一定連不上。
      final result = await ApiClient.ping(baseUrlOverride: 'http://127.0.0.1:9');
      expect(result.ok, isFalse);
      expect(result.kind, ApiErrorKind.network);
      expect(result.message, contains('127.0.0.1:9'));
      expect(result.message, isNot(contains('SocketException')));
    }, skip: !_runApiTests);
  });
}
