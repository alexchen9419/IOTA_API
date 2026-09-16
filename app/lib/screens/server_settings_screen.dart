import 'package:flutter/material.dart';

import '../theme/app_theme.dart';
import '../utils/api_client.dart';
import '../utils/api_config.dart';
import '../utils/app_feedback.dart';

/// 伺服器位址設定。
///
/// 後端閘道跑在開發機的區網 IP 上（`http://<IP>:8091`），這個位址會隨著換
/// 機器或換網路而變。沒有這個畫面的話，每次換位址都要重新編譯 App，
/// 實機測試幾乎沒辦法進行。
class ServerSettingsScreen extends StatefulWidget {
  const ServerSettingsScreen({super.key});

  @override
  State<ServerSettingsScreen> createState() => _ServerSettingsScreenState();
}

class _ServerSettingsScreenState extends State<ServerSettingsScreen> {
  late final TextEditingController _urlController;
  bool _isTesting = false;
  ApiResult? _lastTestResult;

  @override
  void initState() {
    super.initState();
    _urlController = TextEditingController(text: ApiConfig.baseUrl);
  }

  @override
  void dispose() {
    _urlController.dispose();
    super.dispose();
  }

  Future<void> _testConnection() async {
    setState(() {
      _isTesting = true;
      _lastTestResult = null;
    });

    // 測試「輸入框裡的」位址，而不是已存檔的位址 —— 讓使用者可以先驗證再儲存。
    final candidate = _urlController.text.trim();
    final result = await ApiClient.ping(
      baseUrlOverride: candidate.contains('://') ? candidate : 'http://$candidate',
    );

    if (!mounted) return;
    setState(() {
      _isTesting = false;
      _lastTestResult = result;
    });
  }

  Future<void> _save() async {
    final value = _urlController.text.trim();
    if (value.isEmpty) {
      AppFeedback.error(context, '請輸入伺服器位址');
      return;
    }

    await ApiConfig.setBaseUrl(value);
    if (!mounted) return;

    setState(() => _urlController.text = ApiConfig.baseUrl);
    AppFeedback.success(context, '已儲存：${ApiConfig.baseUrl}');
  }

  Future<void> _reset() async {
    await ApiConfig.clearOverride();
    if (!mounted) return;

    setState(() {
      _urlController.text = ApiConfig.baseUrl;
      _lastTestResult = null;
    });
    AppFeedback.info(context, '已還原為預設值');
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(title: const Text('伺服器設定')),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          _buildCurrentCard(),
          const SizedBox(height: AppSpacing.lg),
          TextField(
            controller: _urlController,
            style: const TextStyle(color: AppColors.textPrimary),
            keyboardType: TextInputType.url,
            autocorrect: false,
            decoration: InputDecoration(
              labelText: 'API 伺服器位址',
              labelStyle: TextStyle(color: AppColors.textTertiary),
              hintText: 'http://192.168.1.5:8091',
              hintStyle: TextStyle(color: AppColors.textDisabled),
              prefixIcon: const Icon(Icons.dns_outlined,
                  color: AppColors.indigoLight),
              filled: true,
              fillColor: AppColors.surface.withValues(alpha: 0.6),
              enabledBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(AppRadius.lg),
                borderSide: BorderSide(color: AppColors.border),
              ),
              focusedBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(AppRadius.lg),
                borderSide: const BorderSide(color: AppColors.purple, width: 2),
              ),
            ),
          ),
          const SizedBox(height: AppSpacing.md),
          Row(
            children: [
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: _isTesting ? null : _testConnection,
                  icon: _isTesting
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.wifi_tethering),
                  label: Text(_isTesting ? '測試中…' : '測試連線'),
                  style: OutlinedButton.styleFrom(
                    foregroundColor: AppColors.indigoLight,
                    side: const BorderSide(color: AppColors.indigo),
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(AppRadius.md),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: ElevatedButton.icon(
                  onPressed: _save,
                  icon: const Icon(Icons.save_outlined),
                  label: const Text('儲存'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppColors.indigo,
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(AppRadius.md),
                    ),
                  ),
                ),
              ),
            ],
          ),
          if (_lastTestResult != null) ...[
            const SizedBox(height: AppSpacing.md),
            _buildTestResult(_lastTestResult!),
          ],
          const SizedBox(height: AppSpacing.lg),
          TextButton.icon(
            onPressed: ApiConfig.hasUserOverride ? _reset : null,
            icon: const Icon(Icons.restart_alt),
            label: const Text('還原為預設值'),
            style: TextButton.styleFrom(foregroundColor: AppColors.textTertiary),
          ),
          const Divider(height: AppSpacing.xl * 2),
          _buildHelp(),
        ],
      ),
    );
  }

  Widget _buildCurrentCard() {
    return Container(
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.6),
        borderRadius: BorderRadius.circular(AppRadius.lg),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('目前使用中', style: TextStyle(color: AppColors.textTertiary, fontSize: 12)),
          const SizedBox(height: AppSpacing.xs),
          SelectableText(
            ApiConfig.baseUrl,
            style: const TextStyle(
              color: AppColors.textPrimary,
              fontSize: 16,
              fontWeight: FontWeight.bold,
              fontFamily: 'monospace',
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
            decoration: BoxDecoration(
              color: AppColors.indigo.withValues(alpha: 0.2),
              borderRadius: BorderRadius.circular(AppRadius.sm - 6),
            ),
            child: Text(
              '來源：${ApiConfig.sourceDescription}',
              style: const TextStyle(
                color: AppColors.indigoLight,
                fontSize: 11,
                fontWeight: FontWeight.bold,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildTestResult(ApiResult result) {
    final color = result.ok ? AppColors.success : AppColors.danger;
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
          Icon(result.ok ? Icons.check_circle : Icons.error_outline,
              color: color, size: 20),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Text(
              result.message,
              style: TextStyle(color: color, fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildHelp() {
    const items = [
      ('後端啟動指令',
          'docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d'),
      ('桌面 / 同一台電腦', 'http://localhost:8091'),
      ('Android 模擬器', 'http://10.0.2.2:8091'),
      ('實體手機 / 平板', 'http://<開發機區網IP>:8091'),
    ];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('常見設定',
            style: TextStyle(
                color: AppColors.textSecondary,
                fontSize: 14,
                fontWeight: FontWeight.bold)),
        const SizedBox(height: AppSpacing.sm),
        for (final (label, value) in items)
          Padding(
            padding: const EdgeInsets.only(bottom: AppSpacing.sm),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(label,
                    style:
                        TextStyle(color: AppColors.textTertiary, fontSize: 12)),
                SelectableText(
                  value,
                  style: TextStyle(
                    color: AppColors.textSecondary,
                    fontSize: 12,
                    fontFamily: 'monospace',
                  ),
                ),
              ],
            ),
          ),
        const SizedBox(height: AppSpacing.sm),
        Text(
          '注意：後端目前是明文 HTTP，Android 9 以上與 iOS 預設會封鎖。'
          '本專案已在 network_security_config.xml 與 Info.plist 開放區網網段，'
          '正式部署改用 HTTPS 後應移除這些例外。',
          style: TextStyle(color: AppColors.textDisabled, fontSize: 11, height: 1.5),
        ),
      ],
    );
  }
}
