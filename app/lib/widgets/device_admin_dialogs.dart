import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../theme/app_theme.dart';
import '../utils/api_client.dart';
import '../utils/api_config.dart';
import '../utils/app_feedback.dart';
import '../utils/device_models.dart';
import '../utils/parsing.dart';

/// Admin 專屬的裝置管理對話框：維修模式（UC5.1）、韌體更新（UC2.2）、
/// 除役（UC2.3）、裝置配對（UC2.1）。
///
/// 每個對話框裡 new 出來的 [TextEditingController] 都在 `finally` 釋放 ——
/// 舊程式碼在四處遺漏，每開一次就洩漏一個。
class DeviceAdminDialogs {
  DeviceAdminDialogs._();

  // ------------------------------------------------------------------
  // UC5.1 維修模式
  // ------------------------------------------------------------------

  /// 開啟或關閉維修模式。回傳 true 表示有變更、外層應重新整理。
  static Future<bool> maintenance({
    required BuildContext context,
    required int familyId,
    required String adminUid,
    required Map<String, dynamic> device,
  }) async {
    final isCurrentlyOn = asBool(device['maintenance_mode']);
    final deviceId = asText(device['device_id'], fallback: '');

    if (isCurrentlyOn) {
      final confirmed = await _confirm(
        context: context,
        title: '關閉維修模式',
        message: '關閉後這台裝置會恢復接受日常控制指令（上鎖 / 解鎖）。',
        confirmLabel: '確定關閉',
        confirmColor: AppColors.success,
      );
      if (confirmed != true || !context.mounted) return false;

      final result = await ApiClient.post(ApiEndpoints.maintenanceMode, {
        'family_id': familyId,
        'admin_uid': adminUid,
        'device_id': deviceId,
        'action': 'Disable',
      });
      if (!context.mounted) return false;
      AppFeedback.fromResult(context, result);
      return result.ok;
    }

    final reasonController = TextEditingController(text: '更換電池');
    final durationController = TextEditingController(text: '60');
    final formKey = GlobalKey<FormState>();

    try {
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          title: const Text('開啟維修模式'),
          content: Form(
            key: formKey,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '維修模式期間，這台裝置會拒絕所有日常控制指令。'
                  '系統強制要求設定最長有效時間，到期後會自動恢復。',
                  style:
                      TextStyle(color: AppColors.textTertiary, fontSize: 13),
                ),
                const SizedBox(height: AppSpacing.md),
                TextFormField(
                  controller: durationController,
                  keyboardType: TextInputType.number,
                  inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                  style: const TextStyle(color: AppColors.textPrimary),
                  decoration: _fieldDecoration('持續時間（分鐘）', '例如 60'),
                  validator: (value) {
                    final minutes = int.tryParse(value ?? '');
                    if (minutes == null || minutes <= 0) {
                      return '請輸入大於 0 的分鐘數';
                    }
                    // 後端 MAX_MAINTENANCE_MINUTES 預設 240，超過會回 400。
                    if (minutes > 240) return '不可超過 240 分鐘（系統上限）';
                    return null;
                  },
                ),
                const SizedBox(height: AppSpacing.md),
                TextFormField(
                  controller: reasonController,
                  style: const TextStyle(color: AppColors.textPrimary),
                  decoration: _fieldDecoration('維修原因', '例如：更換電池'),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(false),
              child: Text('取消',
                  style: TextStyle(color: AppColors.textTertiary)),
            ),
            ElevatedButton(
              onPressed: () {
                if (formKey.currentState?.validate() ?? false) {
                  Navigator.of(dialogContext).pop(true);
                }
              },
              style: ElevatedButton.styleFrom(
                backgroundColor: AppColors.warning,
                foregroundColor: Colors.black,
              ),
              child: const Text('開啟維修模式'),
            ),
          ],
        ),
      );

      if (confirmed != true || !context.mounted) return false;

      final result = await ApiClient.post(ApiEndpoints.maintenanceMode, {
        'family_id': familyId,
        'admin_uid': adminUid,
        'device_id': deviceId,
        'action': 'Enable',
        'duration_minutes': int.parse(durationController.text),
        'reason': reasonController.text.trim(),
      });

      if (!context.mounted) return false;
      AppFeedback.fromResult(context, result);
      return result.ok;
    } finally {
      reasonController.dispose();
      durationController.dispose();
    }
  }

  // ------------------------------------------------------------------
  // UC2.2 韌體更新
  // ------------------------------------------------------------------

  static Future<bool> otaUpdate({
    required BuildContext context,
    required int familyId,
    required String adminUid,
    required Map<String, dynamic> device,
  }) async {
    final fileController = TextEditingController();
    final versionController = TextEditingController();
    final formKey = GlobalKey<FormState>();

    try {
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          title: const Text('韌體更新'),
          content: SingleChildScrollView(
            child: Form(
              key: formKey,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // 這段警告是必要的：後端 ota_update.py 明確不檢查檔案是否存在、
                  // 也不比對版本，打錯檔名一樣回「已觸發」成功。
                  Container(
                    padding: const EdgeInsets.all(AppSpacing.sm),
                    decoration: BoxDecoration(
                      color: AppColors.warning.withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(AppRadius.sm),
                      border: Border.all(
                          color: AppColors.warning.withValues(alpha: 0.4)),
                    ),
                    child: const Text(
                      '⚠️ 後端不會驗證韌體檔案是否存在，也不會比對版本。\n'
                      '檔名打錯或忘記簽章時，這裡仍會顯示「已觸發」，'
                      '但裝置端下載或簽章驗證會失敗。請先確認檔案已放進 '
                      'mqtt-server/firmware/ 並用 sign_firmware.py 簽署。',
                      style: TextStyle(
                          color: AppColors.warning, fontSize: 12, height: 1.5),
                    ),
                  ),
                  const SizedBox(height: AppSpacing.md),
                  TextFormField(
                    controller: fileController,
                    style: const TextStyle(color: AppColors.textPrimary),
                    decoration: _fieldDecoration(
                        '韌體檔名', 'SMART-LOCK-V1_1.1.0.bin'),
                    validator: (value) {
                      final text = (value ?? '').trim();
                      if (text.isEmpty) return '請輸入韌體檔名';
                      if (!text.endsWith('.bin')) return '檔名必須以 .bin 結尾';
                      return null;
                    },
                  ),
                  const SizedBox(height: AppSpacing.md),
                  TextFormField(
                    controller: versionController,
                    style: const TextStyle(color: AppColors.textPrimary),
                    decoration: _fieldDecoration('版本號', '1.1.0'),
                    validator: (value) => (value ?? '').trim().isEmpty
                        ? '請輸入版本號'
                        : null,
                  ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(false),
              child: Text('取消',
                  style: TextStyle(color: AppColors.textTertiary)),
            ),
            ElevatedButton(
              onPressed: () {
                if (formKey.currentState?.validate() ?? false) {
                  Navigator.of(dialogContext).pop(true);
                }
              },
              style: ElevatedButton.styleFrom(
                backgroundColor: AppColors.info,
                foregroundColor: Colors.black,
              ),
              child: const Text('觸發更新'),
            ),
          ],
        ),
      );

      if (confirmed != true || !context.mounted) return false;

      final result = await ApiClient.post(ApiEndpoints.otaUpdate, {
        'family_id': familyId,
        'admin_uid': adminUid,
        'device_id': asText(device['device_id'], fallback: ''),
        'firmware_file': fileController.text.trim(),
        'version': versionController.text.trim(),
      });

      if (!context.mounted) return false;

      if (result.ok) {
        // 刻意不說「更新成功」——後端只是把 MQTT 訊息發出去而已。
        AppFeedback.info(
          context,
          '已發送更新指令。實際結果需在裝置端確認（下載與簽章驗證約需數十秒）。',
        );
      } else {
        AppFeedback.fromResult(context, result);
      }
      return result.ok;
    } finally {
      fileController.dispose();
      versionController.dispose();
    }
  }

  // ------------------------------------------------------------------
  // UC2.3 除役
  // ------------------------------------------------------------------

  static Future<bool> decommission({
    required BuildContext context,
    required String operatorUserId,
    required Map<String, dynamic> device,
  }) async {
    final reasonController =
        TextEditingController(text: '安全考量，進行設備除役');

    try {
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          title: const Text('設備除役與安全解綁'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                '除役後設備的 session key 會被清除、狀態標記為 Revoked，'
                '無法再進行控制或資料通訊，且不能直接重新配對。',
                style: TextStyle(color: AppColors.textSecondary, fontSize: 13),
              ),
              const SizedBox(height: AppSpacing.md),
              TextField(
                controller: reasonController,
                style: const TextStyle(color: AppColors.textPrimary),
                decoration: _fieldDecoration('除役原因', ''),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(false),
              child: Text('取消',
                  style: TextStyle(color: AppColors.textTertiary)),
            ),
            ElevatedButton(
              onPressed: () => Navigator.of(dialogContext).pop(true),
              style: ElevatedButton.styleFrom(
                backgroundColor: AppColors.danger,
                foregroundColor: Colors.white,
              ),
              child: const Text('確定除役'),
            ),
          ],
        ),
      );

      if (confirmed != true || !context.mounted) return false;

      final result = await ApiClient.post(ApiEndpoints.decommissionDevice, {
        'device_id': asText(device['device_id'], fallback: ''),
        // 後端讀的是 operator_user_id（也接受 user_id 作為 fallback），
        // 這裡送正式名稱，讓稽核日誌記錄正確的操作者。
        'operator_user_id': operatorUserId,
        'reason': reasonController.text.trim(),
      });

      if (!context.mounted) return false;
      AppFeedback.fromResult(context, result);
      return result.ok;
    } finally {
      reasonController.dispose();
    }
  }

  // ------------------------------------------------------------------
  // UC2.1 裝置配對
  // ------------------------------------------------------------------

  static Future<bool> pairDevice({
    required BuildContext context,
    required int familyId,
    required String ownerUserId,
  }) async {
    final idController = TextEditingController();
    final nameController = TextEditingController(text: '大門智慧鎖');
    final formKey = GlobalKey<FormState>();
    var deviceType = kDeviceModels.first.id;

    try {
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (dialogContext) => StatefulBuilder(
          builder: (context, setDialogState) => AlertDialog(
            title: const Text('新增裝置安全配對'),
            content: SingleChildScrollView(
              child: Form(
                key: formKey,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '系統會與裝置進行 ECDH 金鑰交換，並用 HKDF 派生 session key'
                      '（只儲存雜湊值）。沒有實體裝置時後端會自動模擬一組金鑰，'
                      '方便先測通流程。',
                      style: TextStyle(
                          color: AppColors.textTertiary, fontSize: 12),
                    ),
                    const SizedBox(height: AppSpacing.md),
                    TextFormField(
                      controller: idController,
                      style: const TextStyle(color: AppColors.textPrimary),
                      decoration: _fieldDecoration(
                          '裝置識別碼（MAC）', 'E8:31:CD:82:80:C8'),
                      validator: (value) => (value ?? '').trim().isEmpty
                          ? '請輸入裝置識別碼'
                          : null,
                    ),
                    const SizedBox(height: AppSpacing.md),
                    TextFormField(
                      controller: nameController,
                      style: const TextStyle(color: AppColors.textPrimary),
                      decoration: _fieldDecoration('裝置名稱', ''),
                      validator: (value) => (value ?? '').trim().isEmpty
                          ? '請輸入裝置名稱'
                          : null,
                    ),
                    const SizedBox(height: AppSpacing.md),
                    // 選項來自 mqtt-server/models.yaml（見 device_models.dart）。
                    // 舊版寫死 smart_lock / sensor / camera，後兩者在整套系統
                    // 裡不存在——沒有型號定義、沒有韌體、沒有 handler——選了
                    // 會配對出一台永遠不會回應的裝置。
                    DropdownButtonFormField<String>(
                      initialValue: deviceType,
                      dropdownColor: AppColors.surface,
                      style: const TextStyle(color: AppColors.textPrimary),
                      decoration: _fieldDecoration('裝置型號', ''),
                      items: kDeviceModels
                          .map((m) => DropdownMenuItem(
                                value: m.id,
                                child: Row(
                                  children: [
                                    Icon(m.icon,
                                        size: 16, color: AppColors.purpleLight),
                                    const SizedBox(width: AppSpacing.sm),
                                    Flexible(
                                      child: Text('${m.label}  (${m.id})',
                                          overflow: TextOverflow.ellipsis),
                                    ),
                                  ],
                                ),
                              ))
                          .toList(),
                      onChanged: (value) {
                        if (value != null) {
                          setDialogState(() => deviceType = value);
                        }
                      },
                    ),
                    const SizedBox(height: AppSpacing.sm),
                    Builder(builder: (_) {
                      final m = deviceModelOf(deviceType);
                      return Text(
                        m == null
                            ? ''
                            : '支援功能：${m.featureLabel}\n'
                                '裝置註冊時必須以同一個型號字串送出 home/register，'
                                '否則 mqtt-server 會回「未知型號」。',
                        style: TextStyle(
                            color: AppColors.textDisabled,
                            fontSize: 11,
                            height: 1.5),
                      );
                    }),
                  ],
                ),
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(dialogContext).pop(false),
                child: Text('取消',
                    style: TextStyle(color: AppColors.textTertiary)),
              ),
              ElevatedButton(
                onPressed: () {
                  if (formKey.currentState?.validate() ?? false) {
                    Navigator.of(dialogContext).pop(true);
                  }
                },
                style: ElevatedButton.styleFrom(
                  backgroundColor: AppColors.purple,
                  foregroundColor: Colors.white,
                ),
                child: const Text('啟動配對'),
              ),
            ],
          ),
        ),
      );

      if (confirmed != true || !context.mounted) return false;

      final result = await ApiClient.post(ApiEndpoints.devicePair, {
        'owner_user_id': ownerUserId,
        'family_id': familyId,
        'device_id': idController.text.trim(),
        'device_name': nameController.text.trim(),
        'device_type': deviceType,
      });

      if (!context.mounted) return false;

      if (!result.ok) {
        AppFeedback.fromResult(context, result);
        return false;
      }

      await _showPairingResult(context, result.dataMap);
      return true;
    } finally {
      idController.dispose();
      nameController.dispose();
    }
  }

  static Future<void> _showPairingResult(
    BuildContext context,
    Map<String, dynamic> data,
  ) async {
    if (!context.mounted) return;

    final ledger = data['ledger'];
    final ledgerMap =
        ledger is Map<String, dynamic> ? ledger : const <String, dynamic>{};

    await showDialog<void>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Row(
          children: [
            Icon(Icons.verified_user, color: AppColors.success),
            SizedBox(width: AppSpacing.sm),
            Expanded(child: Text('金鑰協商完成')),
          ],
        ),
        content: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              _kv('裝置 ID', asText(data['device_id'])),
              _kv('配對狀態', asText(data['pairing_status'])),
              if (asBool(data['simulated_device']))
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: AppSpacing.sm),
                  child: Text(
                    '注意：這是後端模擬的裝置金鑰（沒有真實 ESP32 參與），'
                    '僅供流程驗證。',
                    style: TextStyle(
                        color: AppColors.warning, fontSize: 12, height: 1.4),
                  ),
                ),
              const SizedBox(height: AppSpacing.sm),
              _hash('Session Key Hash', data['session_key_hash']),
              _hash('稽核鏈 Command ID', ledgerMap['command_id']),
              _hash('Current Hash', ledgerMap['current_hash']),
            ],
          ),
        ),
        actions: [
          ElevatedButton(
            onPressed: () => Navigator.of(dialogContext).pop(),
            style: ElevatedButton.styleFrom(
              backgroundColor: AppColors.indigo,
              foregroundColor: Colors.white,
            ),
            child: const Text('確定'),
          ),
        ],
      ),
    );
  }

  // ------------------------------------------------------------------
  // 共用小工具
  // ------------------------------------------------------------------

  static Future<bool?> _confirm({
    required BuildContext context,
    required String title,
    required String message,
    required String confirmLabel,
    required Color confirmColor,
  }) {
    return showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(title),
        content: Text(message,
            style: TextStyle(color: AppColors.textSecondary)),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child:
                Text('取消', style: TextStyle(color: AppColors.textTertiary)),
          ),
          ElevatedButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            style: ElevatedButton.styleFrom(
              backgroundColor: confirmColor,
              foregroundColor: Colors.white,
            ),
            child: Text(confirmLabel),
          ),
        ],
      ),
    );
  }

  static InputDecoration _fieldDecoration(String label, String hint) =>
      InputDecoration(
        labelText: label,
        labelStyle: TextStyle(color: AppColors.textTertiary),
        hintText: hint.isEmpty ? null : hint,
        hintStyle: TextStyle(color: AppColors.textDisabled, fontSize: 13),
        enabledBorder: UnderlineInputBorder(
          borderSide: BorderSide(color: AppColors.border),
        ),
        focusedBorder: const UnderlineInputBorder(
          borderSide: BorderSide(color: AppColors.purple),
        ),
      );

  static Widget _kv(String label, String value) => Padding(
        padding: const EdgeInsets.only(bottom: AppSpacing.xs),
        child: RichText(
          text: TextSpan(
            style: const TextStyle(fontSize: 13),
            children: [
              TextSpan(
                  text: '$label: ',
                  style: TextStyle(color: AppColors.textTertiary)),
              TextSpan(
                text: value,
                style: const TextStyle(
                    color: AppColors.textPrimary,
                    fontWeight: FontWeight.bold),
              ),
            ],
          ),
        ),
      );

  static Widget _hash(String label, dynamic value) {
    final text = asText(value, fallback: '');
    if (text.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.sm),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label,
              style: TextStyle(
                  color: AppColors.textTertiary,
                  fontSize: 11,
                  fontWeight: FontWeight.bold)),
          SelectableText(
            text,
            style: TextStyle(
              color: AppColors.textDisabled,
              fontSize: 10,
              fontFamily: 'monospace',
            ),
          ),
        ],
      ),
    );
  }
}
