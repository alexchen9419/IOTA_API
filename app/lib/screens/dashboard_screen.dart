import 'package:flutter/material.dart';

import '../theme/app_theme.dart';
import '../utils/api_client.dart';
import '../utils/api_config.dart';
import '../utils/parsing.dart';
import '../widgets/app_text_field.dart';
import '../widgets/device_card.dart';
import '../widgets/device_control_sheet.dart';
import '../widgets/empty_state.dart';

/// 場域儀表板（UC4.3）。
///
/// 這是真正對應 `/dashboard` 的畫面。原本 `dashboard_screen.dart` 這個檔名被
/// 家庭清單佔用（那支現在叫 `family_list_screen.dart`），讓人誤以為儀表板已經
/// 做好了，實際上這支 API 的資料 —— 電量、訊號強度、連線健康度、遙測歷史 ——
/// 從來沒有任何畫面顯示過。
class DashboardScreen extends StatefulWidget {
  const DashboardScreen({
    super.key,
    required this.familyId,
    required this.familyName,
    required this.currentUserId,
    required this.myRole,
  });

  final int familyId;
  final String familyName;
  final String currentUserId;
  final String myRole;

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  Map<String, dynamic>? _summary;
  List<dynamic> _devices = [];
  bool _isLoading = false;
  String? _error;
  bool _includeHistory = true;

  bool get _canControl => canControlDevices(widget.myRole);

  @override
  void initState() {
    super.initState();
    _fetch();
  }

  Future<void> _fetch() async {
    if (mounted) setState(() => _isLoading = true);

    final result = await ApiClient.post(ApiEndpoints.dashboard, {
      'auth_type': 'user',
      'user_id': widget.currentUserId,
      'family_id': widget.familyId,
      'include_history': _includeHistory,
      'history_limit': 5,
    });

    if (!mounted) return;
    setState(() {
      _isLoading = false;
      if (result.ok) {
        final data = result.dataMap;
        _summary = data['summary'] as Map<String, dynamic>?;
        _devices = data['devices'] as List<dynamic>? ?? [];
        _error = null;
      } else {
        _error = result.message;
      }
    });
  }

  Future<void> _openControlSheet(Map<String, dynamic> device) async {
    await showModalBottomSheet<void>(
      context: context,
      backgroundColor: AppColors.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(AppRadius.xl)),
      ),
      builder: (_) => DeviceControlSheet(
        familyId: widget.familyId,
        currentUserId: widget.currentUserId,
        device: device,
        onStateChanged: _fetch,
      ),
    );
    if (mounted) await _fetch();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('場域儀表板',
                style: TextStyle(fontSize: 17, fontWeight: FontWeight.bold)),
            Text(
              widget.familyName,
              style: TextStyle(fontSize: 12, color: AppColors.textTertiary),
            ),
          ],
        ),
        actions: [
          IconButton(
            tooltip: _includeHistory ? '隱藏遙測歷史' : '顯示遙測歷史',
            icon: Icon(_includeHistory
                ? Icons.timeline
                : Icons.timeline_outlined),
            onPressed: () {
              setState(() => _includeHistory = !_includeHistory);
              _fetch();
            },
          ),
          IconButton(
            tooltip: '重新整理',
            icon: const Icon(Icons.refresh),
            onPressed: _isLoading ? null : _fetch,
          ),
        ],
      ),
      body: Stack(
        children: [
          const Positioned(
            top: -120,
            right: -80,
            child: GlowOrb(color: AppColors.indigo, diameter: 300),
          ),
          RefreshIndicator(
            onRefresh: _fetch,
            color: AppColors.purple,
            backgroundColor: AppColors.surface,
            child: _buildBody(),
          ),
        ],
      ),
    );
  }

  Widget _buildBody() {
    if (_isLoading && _devices.isEmpty && _error == null) {
      return const Center(child: CircularProgressIndicator());
    }

    if (_error != null) {
      return EmptyState(
        icon: Icons.cloud_off_outlined,
        title: '無法載入儀表板',
        subtitle: _error,
        isError: true,
      );
    }

    if (_devices.isEmpty) {
      return const EmptyState(
        icon: Icons.router_outlined,
        title: '這個場域還沒有裝置',
        subtitle: '完成裝置配對後，這裡會顯示即時狀態與連線健康度。',
      );
    }

    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.all(AppSpacing.md),
      children: [
        if (_summary != null) _buildSummary(_summary!),
        const SizedBox(height: AppSpacing.md),
        for (final entry in _devices)
          if (entry is Map<String, dynamic>) _buildDeviceBlock(entry),
      ],
    );
  }

  Widget _buildSummary(Map<String, dynamic> summary) {
    final tiles = [
      (
        '裝置總數',
        asInt(summary['total_devices']) ?? 0,
        AppColors.indigoLight,
        Icons.router_outlined
      ),
      (
        '連線中',
        asInt(summary['online_devices']) ?? 0,
        AppColors.healthGood,
        Icons.wifi
      ),
      (
        '離線',
        asInt(summary['offline_devices']) ?? 0,
        AppColors.healthOffline,
        Icons.wifi_off
      ),
      (
        '異常',
        asInt(summary['fault_devices']) ?? 0,
        AppColors.healthFault,
        Icons.error_outline
      ),
      (
        '低電量',
        asInt(summary['low_battery_devices']) ?? 0,
        AppColors.warning,
        Icons.battery_alert_rounded
      ),
    ];

    return Container(
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.6),
        borderRadius: BorderRadius.circular(AppRadius.xl),
        border: Border.all(color: AppColors.border),
      ),
      // 五格用 Row + spaceAround，數字一破三位數（裝置總數 100）在窄螢幕就
      // overflow。改成 Wrap，空間不夠時自動換行而不是畫出黃黑斜紋。
      child: Wrap(
        alignment: WrapAlignment.spaceAround,
        spacing: AppSpacing.md,
        runSpacing: AppSpacing.md,
        children: [
          for (final (label, value, color, icon) in tiles)
            Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(icon, color: color, size: 18),
                const SizedBox(height: AppSpacing.xs),
                Text(
                  '$value',
                  style: TextStyle(
                    color: color,
                    fontSize: 20,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  label,
                  style:
                      TextStyle(color: AppColors.textDisabled, fontSize: 10),
                ),
              ],
            ),
        ],
      ),
    );
  }

  Widget _buildDeviceBlock(Map<String, dynamic> device) {
    final history = device['history'];
    final historyList = history is List ? history : const [];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        DeviceCard(
          device: device,
          // 儀表板不提供管理選單，維持單一職責：管理動作都在家庭詳情頁。
          isAdmin: false,
          canControl: _canControl,
          myRole: widget.myRole,
          onAction: (action) {
            if (action == DeviceAction.control) _openControlSheet(device);
          },
        ),
        if (device['last_command'] is Map<String, dynamic>)
          _buildLastCommand(device['last_command'] as Map<String, dynamic>),
        if (_includeHistory && historyList.isNotEmpty)
          _buildHistory(historyList),
        const SizedBox(height: AppSpacing.sm),
      ],
    );
  }

  Widget _buildLastCommand(Map<String, dynamic> command) {
    final status = asText(command['status'], fallback: '').toUpperCase();
    final (color, label) = switch (status) {
      'SUCCEEDED' || 'COMPLETED' => (AppColors.success, '已完成'),
      'PUBLISHED' => (AppColors.info, '已發送，等待裝置回報'),
      'ACCEPTED' => (AppColors.info, '已受理'),
      'FAILED' || 'REJECTED' => (AppColors.danger, '失敗'),
      _ => (AppColors.textTertiary, status.isEmpty ? '未知' : status),
    };

    return Container(
      margin: const EdgeInsets.only(bottom: AppSpacing.sm),
      padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.md, vertical: AppSpacing.sm),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.35),
        borderRadius: BorderRadius.circular(AppRadius.md),
      ),
      child: Row(
        children: [
          Icon(Icons.history, size: 14, color: AppColors.textDisabled),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Text(
              '最近指令：${asText(command['action'])}'
              '（${asText(command['actor_id'])}）'
              ' · ${relativeTime(command['created_at'], fallback: '時間未知')}',
              style:
                  TextStyle(color: AppColors.textTertiary, fontSize: 11),
              overflow: TextOverflow.ellipsis,
            ),
          ),
          Text(
            label,
            style: TextStyle(
                color: color, fontSize: 11, fontWeight: FontWeight.bold),
          ),
        ],
      ),
    );
  }

  Widget _buildHistory(List<dynamic> history) {
    return Container(
      margin: const EdgeInsets.only(bottom: AppSpacing.sm),
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.35),
        borderRadius: BorderRadius.circular(AppRadius.md),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '遙測歷史（最近 ${history.length} 筆）',
            style: TextStyle(
              color: AppColors.textTertiary,
              fontSize: 11,
              fontWeight: FontWeight.bold,
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          for (final entry in history)
            if (entry is Map<String, dynamic>)
              Padding(
                padding: const EdgeInsets.only(bottom: AppSpacing.xs),
                child: Row(
                  children: [
                    Container(
                      width: 6,
                      height: 6,
                      decoration: const BoxDecoration(
                        color: AppColors.purple,
                        shape: BoxShape.circle,
                      ),
                    ),
                    const SizedBox(width: AppSpacing.sm),
                    Expanded(
                      child: Text(
                        [
                          physicalStateLabel(entry['physical_state']),
                          if (asInt(entry['battery']) != null)
                            '${asInt(entry['battery'])}%',
                          if (asInt(entry['rssi']) != null)
                            '${asInt(entry['rssi'])} dBm',
                        ].join(' · '),
                        style: TextStyle(
                            color: AppColors.textSecondary, fontSize: 11),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    Text(
                      relativeTime(entry['recorded_at'], fallback: '—'),
                      style: TextStyle(
                          color: AppColors.textDisabled, fontSize: 10),
                    ),
                  ],
                ),
              ),
        ],
      ),
    );
  }
}
