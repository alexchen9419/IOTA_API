// App 的型號清單必須與 mqtt-server/models.yaml 一致。
//
// 這個測試存在的理由：舊版的配對對話框寫死了三個選項——smart_lock、
// sensor（溫濕度感測器）、camera（智慧攝影機）。後兩者在整套系統裡根本不存在，
// 而 models.yaml 裡真正有的 SMART-STRONGBOX-V1 反而選不到。App 端寫死清單、
// 沒有任何東西會在它跟後端脫節時出聲，就會變成那樣。
//
// 這裡直接去讀 mqtt-server/models.yaml，兩邊對不上就讓 flutter test 失敗。
// 用正規表示式抓最上層的鍵值，不為了這件事多裝一個 YAML 套件。

import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:my_app/utils/device_models.dart';

/// 測試從 app/ 底下執行，models.yaml 在上一層的 mqtt-server/。
final File _modelsYaml = File('../mqtt-server/models.yaml');

/// 抓最上層的鍵（行首非空白、結尾是冒號），略過註解。
List<String> _topLevelKeys(String yaml) {
  final re = RegExp(r'^([A-Za-z0-9][A-Za-z0-9._-]*):\s*$', multiLine: true);
  return re.allMatches(yaml).map((m) => m.group(1)!).toList();
}

/// 抓某個型號的 features 陣列，例如 `features: [lock, doorbell]`。
///
/// 用逐行掃描而不是跨行正規表示式：型號名稱含 `-`、yaml 有註解與縮排，
/// 一行一行看比一條看不懂的 regex 好維護。
List<String> _featuresOf(String yaml, String model) {
  final lines = yaml.split('\n');
  var inBlock = false;
  for (final line in lines) {
    if (line.startsWith('$model:')) {
      inBlock = true;
      continue;
    }
    if (!inBlock) continue;
    // 遇到下一個最上層鍵（行首非空白）就結束這個區塊
    if (line.isNotEmpty && !line.startsWith(' ') && !line.startsWith('#')) break;

    final m = RegExp(r'^\s+features:\s*\[([^\]]*)\]').firstMatch(line);
    if (m != null) {
      return m
          .group(1)!
          .split(',')
          .map((s) => s.trim())
          .where((s) => s.isNotEmpty)
          .toList();
    }
  }
  return const [];
}

void main() {
  late String yaml;

  // App 被單獨 clone 出去時（例如 IOTA_frontend）拿不到 mqtt-server/，
  // 那種情況下跳過對照，而不是讓整個測試套件失敗。
  final hasYaml = _modelsYaml.existsSync();
  final skipReason = hasYaml
      ? null
      : '找不到 ${_modelsYaml.path}（App 被單獨 clone 時正常），跳過與 models.yaml 的對照';

  setUpAll(() {
    if (hasYaml) yaml = _modelsYaml.readAsStringSync();
  });

  test('型號清單與 models.yaml 完全一致（含順序）', () {
    expect(
      kDeviceModels.map((m) => m.id).toList(),
      _topLevelKeys(yaml),
      reason: 'mqtt-server/models.yaml 改了就要同步 lib/utils/device_models.dart',
    );
  }, skip: skipReason);

  test('每個型號的 features 與 models.yaml 一致', () {
    for (final m in kDeviceModels) {
      expect(m.features, _featuresOf(yaml, m.id), reason: '${m.id} 的 features 不一致');
    }
  }, skip: skipReason);

  test('不存在的型號不會被誤認', () {
    // 舊版配對對話框提供過的兩個假型號，現在應該一律認不得。
    expect(deviceModelOf('sensor'), isNull);
    expect(deviceModelOf('camera'), isNull);
    expect(deviceModelOf(null), isNull);
    expect(deviceModelOf(''), isNull);
  });

  test('舊資料的 device_type 仍認得，卡片不會變成未知裝置', () {
    expect(deviceModelOf('smart_lock')?.id, 'SMART-LOCK-V1');
    expect(deviceModelOf('SMART-LOCK-V1')?.id, 'SMART-LOCK-V1');
    expect(deviceModelOf('smart-lock-v1')?.id, 'SMART-LOCK-V1');
  });

  test('功能說明有翻譯，不會把英文代碼直接給使用者看', () {
    final lock = kDeviceModels.firstWhere((m) => m.id == 'SMART-LOCK-V1');
    expect(lock.featureLabel, contains('上鎖'));
    expect(lock.featureLabel, isNot(contains('tamper_detect')));
  });
}
