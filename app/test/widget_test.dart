// IOTA App 的單元測試。
//
// 原本這個檔案是 Flutter 範本產生的計數器測試（點 + 號、驗證數字從 0 變 1），
// 測的是 `MyApp` / `MyHomePage` —— 那些範本 widget 在清理死碼時已經移除，
// 所以整個測試無法編譯（flutter analyze 會直接報 error）。
//
// 這裡改成測真正有邏輯、且不需要網路或平台外掛的部分：回應解析工具。

import 'package:flutter_test/flutter_test.dart';
import 'package:my_app/utils/parsing.dart';

void main() {
  group('asInt', () {
    test('原樣回傳 int', () => expect(asInt(42), 42));
    test('字串轉 int', () => expect(asInt('7'), 7));
    test('MySQL 可能回傳的字串數字', () => expect(asInt(' 12 '), 12));
    test('double 取整', () => expect(asInt(3.9), 3));
    test('null 回 null', () => expect(asInt(null), isNull));
    test('無法解析時回 null 而不是丟例外',
        () => expect(asInt('not-a-number'), isNull));
  });

  group('asBool', () {
    test('TINYINT(1) 的 1', () => expect(asBool(1), isTrue));
    test('TINYINT(1) 的 0', () => expect(asBool(0), isFalse));
    test('字串 "true"', () => expect(asBool('true'), isTrue));
    test('null 用預設值', () => expect(asBool(null, defaultValue: true), isTrue));
  });

  group('initialOf', () {
    // 這是舊版 dashboard_screen 的崩潰點：
    // username.toString().substring(0, 1) 遇到空字串會丟 RangeError。
    test('空字串不崩潰，回退到 ?', () => expect(initialOf(''), '?'));
    test('null 不崩潰', () => expect(initialOf(null), '?'));
    test('只有空白字元', () => expect(initialOf('   '), '?'));
    test('中文取第一個字', () => expect(initialOf('陳重旭'), '陳'));
    test('英文轉大寫', () => expect(initialOf('alex'), 'A'));
  });

  group('roleLabel', () {
    test('Admin', () => expect(roleLabel('Admin'), '管理員'));
    test('大小寫不敏感', () => expect(roleLabel('admin'), '管理員'));
    test('Revoked', () => expect(roleLabel('Revoked'), '已撤銷'));
    test('未知角色原樣顯示', () => expect(roleLabel('Wizard'), 'Wizard'));
    test('null 回未知', () => expect(roleLabel(null), '未知'));
  });

  group('asText', () {
    test('null 回退', () => expect(asText(null), '—'));
    test('字串 "null" 也回退', () => expect(asText('null'), '—'));
    test('空字串回退', () => expect(asText(''), '—'));
    test('正常值原樣', () => expect(asText('大門鎖'), '大門鎖'));
  });

  group('formatForApi', () {
    test('輸出後端接受的格式', () {
      final dt = DateTime(2026, 8, 17, 9, 5);
      expect(formatForApi(dt), '2026-08-17 09:05:00');
    });
  });

  group('asDateTime', () {
    test('解析 MySQL DATETIME 字串', () {
      expect(asDateTime('2026-08-17 09:05:00'), DateTime(2026, 8, 17, 9, 5));
    });
    test('壞掉的字串回 null', () => expect(asDateTime('¯\\_(ツ)_/¯'), isNull));
  });

  group('physicalStateLabel', () {
    test('locked', () => expect(physicalStateLabel('LOCKED'), '已上鎖'));
    test('unlocked', () => expect(physicalStateLabel('unlocked'), '已解鎖'));
  });

  // 這兩個判斷原本散在三個畫面各寫一份、規則還不一致：owner 在畫面上顯示
  // 「管理員」也能控制裝置，卻看不到管理選單與發送邀請分頁。集中之後用測試
  // 釘住兩者的差異 —— canControl 比 isAdmin 多包含 member。
  group('角色權限', () {
    test('admin 是管理員', () => expect(isFamilyAdmin('Admin'), isTrue));
    test('owner 也是管理員（大小寫不拘）', () {
      expect(isFamilyAdmin('owner'), isTrue);
      expect(isFamilyAdmin('OWNER'), isTrue);
    });
    test('member 不是管理員但可以控制', () {
      expect(isFamilyAdmin('Member'), isFalse);
      expect(canControlDevices('Member'), isTrue);
    });
    test('guest 兩者皆否', () {
      expect(isFamilyAdmin('Guest'), isFalse);
      expect(canControlDevices('Guest'), isFalse);
    });
    test('revoked 兩者皆否', () {
      expect(isFamilyAdmin('Revoked'), isFalse);
      expect(canControlDevices('Revoked'), isFalse);
    });
    test('null / 空字串不會誤判成有權限', () {
      expect(isFamilyAdmin(null), isFalse);
      expect(canControlDevices(''), isFalse);
    });
  });
}
