import 'dart:async';

import 'package:flutter/material.dart';

import '../theme/app_theme.dart';
import '../utils/api_client.dart';
import '../utils/api_config.dart';
import '../utils/app_feedback.dart';
import '../utils/parsing.dart';

/// 一次控制指令在 UI 上的三個階段。
///
/// `CONTROL_MODE=mqtt` 時後端只回 `PUBLISHED`（已發布到 broker，等裝置非同步
/// 回報），**不代表門真的開了**。舊 UI 完全沒有控制功能，新做的時候必須把這件事
/// 表達清楚，不能按下去就顯示「已解鎖」—— 那會讓使用者以為門開了而離開現場。
enum ControlPhase { idle, sending, waitingForDevice, confirmed, failed }

/// 遠端鎖控面板（UC4.1）。
///
/// 後端 `/control_device` 的驗證鏈：裝置範圍 → 維修模式 → 角色/零信任 policy
/// → 發布 MQTT。任何一關失敗都會回帶訊息的 4xx，這裡直接顯示後端訊息。
class DeviceControlSheet extends StatefulWidget {
  const DeviceControlSheet({
    super.key,
    required this.familyId,
    required this.currentUserId,
    required this.device,
    this.onStateChanged,
  });

  final int familyId;
  final String currentUserId;
  final Map<String, dynamic> device;

  /// 指令確認後通知外層重新整理裝置清單。
  final VoidCallback? onStateChanged;

  @override
  State<DeviceControlSheet> createState() => _DeviceControlSheetState();
}

class _DeviceControlSheetState extends State<DeviceControlSheet> {
  ControlPhase _phase = ControlPhase.idle;
  String? _pendingAction;
  String? _statusMessage;
  String? _commandId;

  /// 輪詢裝置狀態的計時器。沒有 WebSocket 推播，只能主動查。
  Timer? _pollTimer;
  int _pollAttempts = 0;

  /// 最多輪詢幾次（每 2 秒一次 = 最長 30 秒）。
  static const int _maxPollAttempts = 15;

  /// 從外層帶進來的即時狀態，會被輪詢結果覆寫。
  late Map<String, dynamic> _device;

  @override
  void initState() {
    super.initState();
    _device = Map<String, dynamic>.from(widget.device);
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    super.dispose();
  }

  bool get _isBusy =>
      _phase == ControlPhase.sending || _phase == ControlPhase.waitingForDevice;

  bool get _inMaintenance => asBool(_device['maintenance_mode']);

  String get _deviceId => asText(_device['device_id'], fallback: '');

  Future<void> _sendCommand(String action) async {
    if (_isBusy) return;

    setState(() {
      _phase = ControlPhase.sending;
      _pendingAction = action;
      _statusMessage = null;
      _commandId = null;
    });

    final result = await ApiClient.post(
      ApiEndpoints.controlDevice,
      {
        'family_id': widget.familyId,
        'device_id': _deviceId,
        'action': action,
        'auth_type': 'user',
        'user_id': widget.currentUserId,
        'parameters': const <String, dynamic>{},
      },
      timeoutOverride: ApiClient.controlTimeout,
    );

    if (!mounted) return;

    if (!result.ok) {
      setState(() {
        _phase = ControlPhase.failed;
        _statusMessage = result.message;
      });
      AppFeedback.fromResult(context, result);
      return;
    }

    final data = result.dataMap;
    final commandStatus = asText(data['command_status'], fallback: '');
    _commandId = asText(data['command_id'], fallback: '');

    // mock 模式會直接回 SUCCEEDED / COMPLETED，那就不用等裝置。
    if (commandStatus == 'PUBLISHED') {
      setState(() {
        _phase = ControlPhase.waitingForDevice;
        _statusMessage = '指令已送達閘道器，等待裝置回報執行結果…';
      });
      _startPolling();
    } else {
      setState(() {
        _phase = ControlPhase.confirmed;
        _statusMessage = result.message;
      });
      widget.onStateChanged?.call();
      await _refreshDevice();
    }
  }

  void _startPolling() {
    _pollAttempts = 0;
    _pollTimer?.cancel();
    _pollTimer = Timer.periodic(const Duration(seconds: 2), (timer) async {
      _pollAttempts++;

      if (!mounted) {
        timer.cancel();
        return;
      }

      final confirmed = await _refreshDevice();

      if (!mounted) {
        timer.cancel();
        return;
      }

      if (confirmed) {
        timer.cancel();
        setState(() {
          _phase = ControlPhase.confirmed;
          _statusMessage = '裝置已回報執行完成';
        });
        widget.onStateChanged?.call();
        return;
      }

      if (_pollAttempts >= _maxPollAttempts) {
        timer.cancel();
        setState(() {
          _phase = ControlPhase.failed;
          // 這裡刻意不說「失敗」—— 指令確實發出去了，只是裝置沒回報。
          // 實體 ESP32 離線或韌體沒回 state 時就是這個情況。
          _statusMessage = '指令已發送，但裝置在 30 秒內沒有回報狀態。'
              '請確認裝置電源與網路連線，或稍後在儀表板查看最新狀態。';
        });
        widget.onStateChanged?.call();
      }
    });
  }

  /// 查一次這筆指令的執行狀態，回傳「指令是否已經完成」。
  ///
  /// 走 `/get_command_status` 而不是 `/dashboard`：後者每次呼叫都會寫一筆
  /// DASHBOARD_VIEWED 進 audit_logs，用它輪詢等於每按一次解鎖就往雜湊鏈裡
  /// 塞最多 15 筆假的稽核紀錄。詳見 API/get_command_status/。
  Future<bool> _refreshDevice() async {
    final commandId = _commandId;
    if (commandId == null || commandId.isEmpty) return false;

    final result = await ApiClient.post(ApiEndpoints.getCommandStatus, {
      'command_id': commandId,
      'user_id': widget.currentUserId,
    });

    if (!mounted || !result.ok) return false;

    final data = result.dataMap;
    final telemetry = data['telemetry'];
    if (telemetry is Map<String, dynamic>) {
      // 只覆寫遙測會更新的欄位，保留 device_name / maintenance_mode 等
      // 從外層帶進來、這支端點不回傳的資料。
      setState(() {
        _device = {
          ..._device,
          if (telemetry['physical_state'] != null)
            'physical_state': telemetry['physical_state'],
          if (telemetry['battery'] != null) 'battery': telemetry['battery'],
          if (telemetry['rssi'] != null) 'rssi': telemetry['rssi'],
        };
      });
    }

    final command = data['command'];
    if (command is! Map<String, dynamic>) return false;

    final status = asText(command['status'], fallback: '').toUpperCase();
    // device_status_update / mqtt_topic_bridge 寫回來之後才會離開 PUBLISHED。
    return status.isNotEmpty &&
        status != 'PUBLISHED' &&
        status != 'ACCEPTED';
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(
        left: AppSpacing.lg,
        right: AppSpacing.lg,
        top: AppSpacing.lg,
        bottom: MediaQuery.of(context).viewInsets.bottom + AppSpacing.lg,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Center(
            child: Container(
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: AppColors.borderStrong,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
          ),
          const SizedBox(height: AppSpacing.lg),
          _buildHeader(),
          const SizedBox(height: AppSpacing.lg),
          if (_inMaintenance) _buildMaintenanceBanner() else _buildControls(),
          if (_statusMessage != null) ...[
            const SizedBox(height: AppSpacing.md),
            _buildStatusBanner(),
          ],
          const SizedBox(height: AppSpacing.md),
          _buildFooterNote(),
        ],
      ),
    );
  }

  Widget _buildHeader() {
    final state = asText(_device['physical_state'], fallback: '');
    final health = asText(_device['connection_health'], fallback: 'UNKNOWN');

    return Row(
      children: [
        Container(
          width: 52,
          height: 52,
          decoration: BoxDecoration(
            color: AppColors.purple.withValues(alpha: 0.15),
            borderRadius: BorderRadius.circular(AppRadius.lg),
          ),
          child: Icon(
            state.toLowerCase() == 'unlocked'
                ? Icons.lock_open_rounded
                : Icons.lock_rounded,
            color: AppColors.purpleLight,
            size: 26,
          ),
        ),
        const SizedBox(width: AppSpacing.md),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                asText(_device['device_name'], fallback: '未命名裝置'),
                style: const TextStyle(
                  color: AppColors.textPrimary,
                  fontSize: 18,
                  fontWeight: FontWeight.bold,
                ),
                overflow: TextOverflow.ellipsis,
              ),
              const SizedBox(height: AppSpacing.xs),
              Row(
                children: [
                  Text(
                    state.isEmpty ? '狀態未知' : physicalStateLabel(state),
                    style: TextStyle(
                      color: AppColors.textSecondary,
                      fontSize: 13,
                    ),
                  ),
                  const SizedBox(width: AppSpacing.sm),
                  _healthDot(health),
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _healthDot(String health) {
    final (color, label) = switch (health.toUpperCase()) {
      'GOOD' => (AppColors.healthGood, '連線良好'),
      'WEAK' => (AppColors.healthWeak, '訊號弱'),
      'OFFLINE' => (AppColors.healthOffline, '離線'),
      'FAULT' => (AppColors.healthFault, '異常'),
      'NO_DATA' => (AppColors.healthOffline, '尚無資料'),
      _ => (AppColors.healthOffline, '未知'),
    };

    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
        ),
        const SizedBox(width: AppSpacing.xs),
        Text(label, style: TextStyle(color: color, fontSize: 12)),
      ],
    );
  }

  Widget _buildControls() {
    // 韌體目前只認得 lock / unlock；control_device.py 雖然還支援 ON/OFF/OPEN…
    // 但 mqtt_topic_bridge.py 會把不支援的動作直接丟棄，所以不提供那些按鈕。
    return Row(
      children: [
        Expanded(
          child: _actionButton(
            action: 'LOCK',
            label: '上鎖',
            icon: Icons.lock_rounded,
            color: AppColors.indigo,
          ),
        ),
        const SizedBox(width: AppSpacing.md),
        Expanded(
          child: _actionButton(
            action: 'UNLOCK',
            label: '解鎖',
            icon: Icons.lock_open_rounded,
            color: AppColors.success,
          ),
        ),
      ],
    );
  }

  Widget _actionButton({
    required String action,
    required String label,
    required IconData icon,
    required Color color,
  }) {
    final isThisPending = _isBusy && _pendingAction == action;

    return SizedBox(
      height: 64,
      child: ElevatedButton(
        // 進行中時兩個按鈕都禁用，避免連點送出重複指令。
        onPressed: _isBusy ? null : () => _sendCommand(action),
        style: ElevatedButton.styleFrom(
          backgroundColor: color,
          foregroundColor: Colors.white,
          disabledBackgroundColor: color.withValues(alpha: 0.3),
          disabledForegroundColor: Colors.white70,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AppRadius.lg),
          ),
        ),
        child: isThisPending
            ? const SizedBox(
                width: 22,
                height: 22,
                child: CircularProgressIndicator(
                    strokeWidth: 2.5, color: Colors.white),
              )
            : Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(icon, size: 22),
                  const SizedBox(height: 2),
                  Text(label,
                      style: const TextStyle(
                          fontSize: 14, fontWeight: FontWeight.bold)),
                ],
              ),
      ),
    );
  }

  Widget _buildMaintenanceBanner() {
    final until = formatDisplay(_device['maintenance_expires_at']);
    final reason = asText(_device['maintenance_reason'], fallback: '未提供原因');

    return Container(
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.warning.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(AppRadius.md),
        border: Border.all(color: AppColors.warning.withValues(alpha: 0.4)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.build_circle_outlined,
              color: AppColors.warning, size: 22),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  '維修模式進行中，已暫停日常控制',
                  style: TextStyle(
                    color: AppColors.warning,
                    fontWeight: FontWeight.bold,
                    fontSize: 14,
                  ),
                ),
                const SizedBox(height: AppSpacing.xs),
                Text(
                  '原因：$reason\n預計恢復：$until',
                  style: const TextStyle(
                      color: AppColors.warning, fontSize: 12, height: 1.5),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildStatusBanner() {
    final (color, icon) = switch (_phase) {
      ControlPhase.confirmed => (AppColors.success, Icons.check_circle_outline),
      ControlPhase.failed => (AppColors.warning, Icons.info_outline),
      _ => (AppColors.info, Icons.sync),
    };

    return Container(
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(AppRadius.md),
        border: Border.all(color: color.withValues(alpha: 0.4)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (_phase == ControlPhase.waitingForDevice)
            SizedBox(
              width: 18,
              height: 18,
              child: CircularProgressIndicator(strokeWidth: 2, color: color),
            )
          else
            Icon(icon, color: color, size: 20),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Text(
              _statusMessage ?? '',
              style: TextStyle(color: color, fontSize: 12, height: 1.5),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildFooterNote() {
    return Text(
      '控制指令會經過閘道器的零信任政策驗證後，透過 MQTT 下發給裝置。'
      '顯示「已回報」之前，請勿假設門鎖已完成動作。',
      style: TextStyle(
        color: AppColors.textDisabled,
        fontSize: 11,
        height: 1.5,
      ),
    );
  }
}
