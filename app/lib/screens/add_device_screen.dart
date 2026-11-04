import 'dart:async';

import 'package:flutter/material.dart';

import '../theme/app_theme.dart';
import '../utils/api_client.dart';
import '../utils/api_config.dart';
import '../utils/app_feedback.dart';
import '../utils/device_models.dart';
import '../utils/parsing.dart';
import '../widgets/device_admin_dialogs.dart';
import '../widgets/empty_state.dart';

/// 新增裝置：免輸入 MAC 的廣播配對（docs/PAIRING.md v1）。
///
/// 流程
///   1. 使用者在鎖上長按進入配對模式，裝置每 3 秒發 `home/pair/announce`。
///   2. 這頁每 3 秒輪詢 mqtt-server 的 `GET /pair/pending`，列出附近的裝置。
///   3. 點選並命名 → `POST /pair/accept/<mac>`，mqtt-server 對該裝置送定向 accept。
///   4. 裝置回 `home/pair/<mac>/result ok:true` 後，mqtt-server 會把它移出 pending；
///      這頁看到它從清單消失，才呼叫 `/device_pair` 寫入資料庫與稽核鏈。
///   5. [_claimTimeout] 內沒消失 → 視為裝置沒回應，`POST /pair/cancel/<mac>`，不寫 DB。
///
/// 規格裡的 `/device_claim`（API 端包辦 accept → 等 result → 寫 DB）還沒實作，
/// 所以第 4 步暫時由 App 編排；之後後端補上時，這頁只要把 [_claim] 換成一次呼叫。
///
/// 已知限制：裝置若在 accept 後斷線超過 PENDING_TTL（90 秒）也會從清單消失，
/// 但 [_claimTimeout] 比 TTL 短得多，正常情況不會誤判。
class AddDeviceScreen extends StatefulWidget {
  const AddDeviceScreen({
    super.key,
    required this.familyId,
    required this.ownerUserId,
  });

  final int familyId;
  final String ownerUserId;

  @override
  State<AddDeviceScreen> createState() => _AddDeviceScreenState();
}

enum _Phase { scanning, claiming }

class _AddDeviceScreenState extends State<AddDeviceScreen> {
  static const Duration _pollInterval = Duration(seconds: 3);
  static const Duration _claimTimeout = Duration(seconds: 30);

  Timer? _pollTimer;
  bool _polling = false;

  List<Map<String, dynamic>> _devices = const [];
  String? _scanError;
  bool _firstLoad = true;

  _Phase _phase = _Phase.scanning;
  String _claimStatus = '';

  @override
  void initState() {
    super.initState();
    _poll();
    _pollTimer = Timer.periodic(_pollInterval, (_) => _poll());
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    super.dispose();
  }

  Future<List<Map<String, dynamic>>?> _fetchPending() async {
    final result = await ApiClient.rawJson(ApiConfig.pairingUri('pair/pending'));
    if (!result.ok) {
      if (mounted) setState(() => _scanError = result.message);
      return null;
    }
    final list = result.dataMap['devices'];
    return list is List ? list.whereType<Map<String, dynamic>>().toList() : const [];
  }

  Future<void> _poll() async {
    if (_polling || _phase != _Phase.scanning) return;
    _polling = true;
    final devices = await _fetchPending();
    _polling = false;
    if (!mounted || devices == null) return;
    setState(() {
      _devices = devices;
      _scanError = null;
      _firstLoad = false;
    });
  }

  Future<void> _onDeviceTap(Map<String, dynamic> device) async {
    final mac = asText(device['mac'], fallback: '');
    if (mac.isEmpty) return;
    final model = deviceModelOf(device['model']);
    final suffix = mac.replaceAll(':', '');
    final defaultName =
        '${model?.label ?? '裝置'} ${suffix.substring(suffix.length - 4)}';

    final name = await _askName(mac, model, defaultName);
    if (name == null || !mounted) return;
    await _claim(mac, name, model?.id ?? kDeviceModels.first.id);
  }

  Future<String?> _askName(String mac, DeviceModel? model, String defaultName) {
    final controller = TextEditingController(text: defaultName);
    final formKey = GlobalKey<FormState>();
    return showDialog<String>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('加入這台裝置'),
        content: Form(
          key: formKey,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                '${model?.label ?? '未知型號'}  ·  $mac',
                style: TextStyle(color: AppColors.textTertiary, fontSize: 12),
              ),
              const SizedBox(height: AppSpacing.md),
              TextFormField(
                controller: controller,
                autofocus: true,
                maxLength: 40,
                decoration: const InputDecoration(labelText: '裝置名稱'),
                validator: (value) =>
                    (value ?? '').trim().isEmpty ? '請輸入裝置名稱' : null,
              ),
              if (model == null)
                Text(
                  '這個型號不在 App 的型號清單，會以「${kDeviceModels.first.label}」登記。',
                  style: const TextStyle(color: AppColors.warning, fontSize: 11),
                ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(),
            child: Text('取消', style: TextStyle(color: AppColors.textTertiary)),
          ),
          ElevatedButton(
            onPressed: () {
              if (formKey.currentState?.validate() ?? false) {
                Navigator.of(dialogContext).pop(controller.text.trim());
              }
            },
            style: ElevatedButton.styleFrom(
              backgroundColor: AppColors.purple,
              foregroundColor: Colors.white,
            ),
            child: const Text('加入'),
          ),
        ],
      ),
    ).whenComplete(controller.dispose);
  }

  Future<void> _claim(String mac, String name, String deviceType) async {
    setState(() {
      _phase = _Phase.claiming;
      _claimStatus = '正在通知裝置…';
    });

    // 3. 定向 accept
    final accept = await ApiClient.rawJson(
      ApiConfig.pairingUri('pair/accept/$mac'),
      method: 'POST',
      body: {'family_id': widget.familyId, 'device_name': name},
    );
    if (!mounted) return;
    if (!accept.ok) {
      _finishWithError(accept.statusCode == 400
          ? '找不到這台裝置，請重新掃描'
          : accept.message);
      return;
    }

    // 4. 等裝置回報（從 pending 消失）
    setState(() => _claimStatus = '配對中…請保持裝置在配對模式');
    final deadline = DateTime.now().add(_claimTimeout);
    var confirmed = false;
    while (DateTime.now().isBefore(deadline)) {
      await Future<void>.delayed(const Duration(seconds: 2));
      if (!mounted) return;
      final pending = await _fetchPending();
      if (!mounted) return;
      if (pending != null && !pending.any((d) => d['mac'] == mac)) {
        confirmed = true;
        break;
      }
    }

    if (!confirmed) {
      // 未收到 result：清掉 retained accept，不寫 DB（PAIRING.md §2）
      await ApiClient.rawJson(ApiConfig.pairingUri('pair/cancel/$mac'),
          method: 'POST');
      if (!mounted) return;
      _finishWithError('裝置沒有回應，已取消。請在鎖上重新長按 5 秒後再試一次');
      return;
    }

    // 5. 裝置確認了，寫入資料庫與稽核鏈
    setState(() => _claimStatus = '登記到場域…');
    final pair = await ApiClient.post(ApiEndpoints.devicePair, {
      'owner_user_id': widget.ownerUserId,
      'family_id': widget.familyId,
      'device_id': mac,
      'device_name': name,
      'device_type': deviceType,
    });
    if (!mounted) return;

    if (!pair.ok) {
      // 409 有兩種：已綁定其他場域、已除役，後端訊息已經說清楚，直接顯示。
      _finishWithError(pair.statusCode == 409
          ? pair.message
          : '裝置已回應，但登記失敗：${pair.message}');
      return;
    }

    await DeviceAdminDialogs.showPairingResult(context, pair.dataMap);
    if (mounted) Navigator.of(context).pop(true);
  }

  void _finishWithError(String message) {
    AppFeedback.error(context, message);
    setState(() => _phase = _Phase.scanning);
    _poll();
  }

  Future<void> _manualPair() async {
    final paired = await DeviceAdminDialogs.pairDevice(
      context: context,
      familyId: widget.familyId,
      ownerUserId: widget.ownerUserId,
    );
    if (paired && mounted) Navigator.of(context).pop(true);
  }

  @override
  Widget build(BuildContext context) {
    final claiming = _phase == _Phase.claiming;
    return PopScope(
      // 配對進行中離開會留下 retained accept，等它跑完或失敗再離開
      canPop: !claiming,
      child: Scaffold(
        backgroundColor: AppColors.background,
        appBar: AppBar(title: const Text('新增裝置')),
        body: claiming ? _buildClaiming() : _buildScanning(),
      ),
    );
  }

  Widget _buildClaiming() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.xl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const CircularProgressIndicator(),
            const SizedBox(height: AppSpacing.lg),
            Text(
              _claimStatus,
              textAlign: TextAlign.center,
              style: const TextStyle(color: AppColors.textPrimary, fontSize: 15),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildScanning() {
    return RefreshIndicator(
      onRefresh: _poll,
      child: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          Container(
            padding: const EdgeInsets.all(AppSpacing.md),
            decoration: BoxDecoration(
              color: AppColors.surface.withValues(alpha: 0.6),
              borderRadius: BorderRadius.circular(AppRadius.lg),
              border: Border.all(color: AppColors.border),
            ),
            child: Row(
              children: [
                const Icon(Icons.touch_app_outlined,
                    color: AppColors.indigoLight, size: 28),
                const SizedBox(width: AppSpacing.md),
                Expanded(
                  child: Text(
                    '在門鎖上長按配對鍵 5 秒，指示燈開始慢閃後，裝置會出現在下方。'
                    '配對模式維持約 90 秒。',
                    style: TextStyle(
                        color: AppColors.textSecondary, fontSize: 13, height: 1.5),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: AppSpacing.lg),
          Row(
            children: [
              const SizedBox(
                width: 14,
                height: 14,
                child: CircularProgressIndicator(strokeWidth: 2),
              ),
              const SizedBox(width: AppSpacing.sm),
              Text(
                _devices.isEmpty ? '正在尋找附近的裝置…' : '發現 ${_devices.length} 台裝置',
                style: TextStyle(color: AppColors.textTertiary, fontSize: 13),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.md),
          ..._buildDeviceList(),
          const SizedBox(height: AppSpacing.xl),
          Center(
            child: TextButton.icon(
              onPressed: _manualPair,
              icon: const Icon(Icons.keyboard_outlined, size: 18),
              label: const Text('找不到裝置？改用手動輸入 MAC'),
              style: TextButton.styleFrom(foregroundColor: AppColors.indigoLight),
            ),
          ),
        ],
      ),
    );
  }

  List<Widget> _buildDeviceList() {
    if (_scanError != null && _devices.isEmpty) {
      return [
        EmptyState(
          icon: Icons.wifi_off_outlined,
          title: '連不上配對服務',
          subtitle: '$_scanError\n配對服務在 ${ApiConfig.pairingUri('').authority}'
              '（mqtt-server），請確認它正在執行且防火牆開放 ${ApiConfig.pairingPort}。',
          isError: true,
          action: OutlinedButton(onPressed: _poll, child: const Text('重試')),
        ),
      ];
    }
    if (_firstLoad) return const [];
    if (_devices.isEmpty) {
      return const [
        EmptyState(
          icon: Icons.sensors_outlined,
          title: '附近沒有待配對的裝置',
          subtitle: '確認門鎖已進入配對模式（指示燈慢閃），清單每 3 秒會自動更新。',
        ),
      ];
    }
    return _devices.map(_buildDeviceCard).toList();
  }

  Widget _buildDeviceCard(Map<String, dynamic> device) {
    final mac = asText(device['mac']);
    final model = deviceModelOf(device['model']);
    final fw = asText(device['fw'], fallback: '');
    final age = device['age'];
    final ageText = age is num ? '${age.round()} 秒前出現' : '';

    return Container(
      margin: const EdgeInsets.only(bottom: AppSpacing.md),
      child: Material(
        color: AppColors.surface.withValues(alpha: 0.6),
        borderRadius: BorderRadius.circular(AppRadius.lg),
        child: InkWell(
          borderRadius: BorderRadius.circular(AppRadius.lg),
          onTap: () => _onDeviceTap(device),
          child: Container(
            padding: const EdgeInsets.all(AppSpacing.md),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(AppRadius.lg),
              border: Border.all(color: AppColors.border),
            ),
            child: Row(
              children: [
                Icon(model?.icon ?? Icons.devices_other,
                    color: AppColors.purpleLight, size: 30),
                const SizedBox(width: AppSpacing.md),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        model?.label ?? asText(device['model'], fallback: '未知型號'),
                        style: const TextStyle(
                            color: AppColors.textPrimary,
                            fontSize: 15,
                            fontWeight: FontWeight.bold),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        [mac, if (fw.isNotEmpty) 'fw $fw', if (ageText.isNotEmpty) ageText]
                            .join('  ·  '),
                        style: TextStyle(
                            color: AppColors.textTertiary,
                            fontSize: 11,
                            fontFamily: 'monospace'),
                      ),
                    ],
                  ),
                ),
                const Icon(Icons.chevron_right, color: AppColors.indigoLight),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
