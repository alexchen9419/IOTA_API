/// 系統支援的裝置型號。
///
/// **單一事實來源是 `mqtt-server/models.yaml`**，這裡只是 App 端的副本。
/// `test/device_models_test.dart` 會去讀那份 yaml 並比對，兩邊不一致時
/// `flutter test` 會失敗——所以新增型號時改 yaml、跑一次測試就知道要補哪裡。
///
/// 為什麼需要對齊：
/// 舊版的配對對話框寫死三個選項——`smart_lock`、`sensor`（溫濕度感測器）、
/// `camera`（智慧攝影機）。後兩者在整套系統裡**根本不存在**：models.yaml 沒有、
/// 沒有韌體、`mqtt-server/handlers/` 也沒有對應模組。選了它們一樣會配對成功、
/// 卡片也會出現，但永遠不會有實體裝置回應，控制一律逾時。
/// 反過來 `SMART-STRONGBOX-V1`（保險箱）有 handler、有韌體型號，App 卻選不到。
///
/// `id` 直接用 models.yaml 的鍵值，也就是韌體 `#define MODEL` 的字串、
/// 以及裝置註冊時送進 `home/register` 的 `model`。讓 `devices` 表的
/// `device_type` 與 `devices.json` 的 `model` 用同一套詞彙，兩邊才對得起來。
library;

import 'package:flutter/material.dart';

class DeviceModel {
  const DeviceModel({
    required this.id,
    required this.label,
    required this.features,
    required this.icon,
  });

  /// models.yaml 的鍵值，例如 `SMART-LOCK-V1`。
  final String id;

  /// 顯示名稱。
  final String label;

  /// models.yaml 的 `features`，用來說明這個型號做得到什麼。
  final List<String> features;

  final IconData icon;

  /// 中文的功能說明，配對時顯示給使用者看。
  String get featureLabel =>
      features.map((f) => _featureLabels[f] ?? f).join('、');
}

const Map<String, String> _featureLabels = {
  'lock': '上鎖 / 解鎖',
  'doorbell': '門鈴',
  'tamper_detect': '防拆偵測',
  'alarm': '警報',
};

/// 對應 `mqtt-server/models.yaml`，順序與該檔案一致。
const List<DeviceModel> kDeviceModels = [
  DeviceModel(
    id: 'SMART-LOCK-V1',
    label: '智慧電子鎖',
    features: ['lock', 'doorbell', 'tamper_detect'],
    icon: Icons.lock_outline,
  ),
  DeviceModel(
    id: 'SMART-STRONGBOX-V1',
    label: '智慧保險箱',
    features: ['lock', 'alarm', 'tamper_detect'],
    icon: Icons.inventory_2_outlined,
  ),
];

/// 依 `device_type` 取型號定義；認不得就回 `null`。
///
/// 舊資料的 `device_type` 是 `smart_lock` 這類小寫字串（配對對話框改成送
/// models.yaml 的鍵值之前寫進去的），這裡一併認得，避免既有裝置的卡片
/// 變成「未知裝置」。
DeviceModel? deviceModelOf(dynamic deviceType) {
  final raw = (deviceType ?? '').toString().trim();
  if (raw.isEmpty) return null;
  final upper = raw.toUpperCase();

  for (final m in kDeviceModels) {
    if (m.id.toUpperCase() == upper) return m;
  }

  // 舊值對應
  switch (raw.toLowerCase()) {
    case 'smart_lock':
    case 'lock':
      return kDeviceModels[0];
    case 'strongbox':
    case 'smart_strongbox':
      return kDeviceModels[1];
  }
  return null;
}
