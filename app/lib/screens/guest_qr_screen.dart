import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:qr_flutter/qr_flutter.dart';

import '../theme/app_theme.dart';
import '../utils/api_client.dart';
import '../utils/api_config.dart';
import '../utils/app_feedback.dart';
import '../utils/parsing.dart';
import '../widgets/app_text_field.dart';

/// 訪客臨時授權 QR（UC3.4）。
///
/// 呼叫 `/generate_guest_qr`：後端會重用閒置的 `guest_%` 帳號或建立新的，
/// 設定有效期與使用次數上限，回傳**明文密碼**與 `control_url`。
///
/// 兩個必須讓使用者知道的限制：
///   1. 明文密碼只會出現這一次，離開畫面就查不回來。
///   2. 後端回傳的 `control_url` 目前是佔位網域 `https://your-domain.com/...`，
///      不是真實部署位置，掃了不會通 —— 這裡如實標示，不假裝它可用。
class GuestQrScreen extends StatefulWidget {
  const GuestQrScreen({
    super.key,
    required this.familyId,
    required this.familyName,
    required this.adminUid,
  });

  final int familyId;
  final String familyName;
  final String adminUid;

  @override
  State<GuestQrScreen> createState() => _GuestQrScreenState();
}

class _GuestQrScreenState extends State<GuestQrScreen> {
  final _formKey = GlobalKey<FormState>();
  final _maxUsesController = TextEditingController(text: '3');

  DateTime _endTime = DateTime.now().add(const Duration(hours: 4));
  bool _isGenerating = false;
  Map<String, dynamic>? _result;

  @override
  void dispose() {
    _maxUsesController.dispose();
    super.dispose();
  }

  Future<void> _pickEndTime() async {
    final date = await showDatePicker(
      context: context,
      initialDate: _endTime,
      firstDate: DateTime.now(),
      lastDate: DateTime.now().add(const Duration(days: 90)),
    );
    if (date == null || !mounted) return;

    final time = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.fromDateTime(_endTime),
    );
    if (time == null || !mounted) return;

    setState(() {
      _endTime =
          DateTime(date.year, date.month, date.day, time.hour, time.minute);
    });
  }

  Future<void> _generate() async {
    if (!(_formKey.currentState?.validate() ?? false)) return;
    if (_isGenerating) return;

    setState(() => _isGenerating = true);

    final result = await ApiClient.post(ApiEndpoints.generateGuestQr, {
      'family_id': widget.familyId,
      'admin_uid': widget.adminUid,
      'start_time': null,
      'end_time': formatForApi(_endTime),
      'max_uses': int.tryParse(_maxUsesController.text) ?? 1,
    });

    if (!mounted) return;
    setState(() {
      _isGenerating = false;
      if (result.ok) _result = result.dataMap;
    });

    if (!result.ok) AppFeedback.fromResult(context, result);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(title: const Text('訪客臨時授權')),
      body: ListView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        children: [
          if (_result == null) ..._buildForm() else ..._buildResult(_result!),
        ],
      ),
    );
  }

  List<Widget> _buildForm() {
    return [
      Text(
        '為「${widget.familyName}」產生一組短效期訪客帳號。'
        '訪客可在期限內、於次數上限內操作裝置。',
        style:
            TextStyle(color: AppColors.textTertiary, fontSize: 13, height: 1.5),
      ),
      const SizedBox(height: AppSpacing.lg),
      Form(
        key: _formKey,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            InkWell(
              onTap: _pickEndTime,
              borderRadius: BorderRadius.circular(AppRadius.lg),
              child: Container(
                padding: const EdgeInsets.all(AppSpacing.md),
                decoration: BoxDecoration(
                  color: AppColors.surface.withValues(alpha: 0.6),
                  borderRadius: BorderRadius.circular(AppRadius.lg),
                  border: Border.all(color: AppColors.border),
                ),
                child: Row(
                  children: [
                    const Icon(Icons.event_busy_outlined,
                        color: AppColors.indigoLight),
                    const SizedBox(width: AppSpacing.md),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('有效期限',
                              style: TextStyle(
                                  color: AppColors.textTertiary,
                                  fontSize: 12)),
                          const SizedBox(height: 2),
                          Text(
                            formatDisplay(_endTime),
                            style: const TextStyle(
                              color: AppColors.textPrimary,
                              fontSize: 15,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ],
                      ),
                    ),
                    Icon(Icons.edit_calendar_outlined,
                        color: AppColors.textTertiary, size: 20),
                  ],
                ),
              ),
            ),
            const SizedBox(height: AppSpacing.md),
            AppTextField(
              controller: _maxUsesController,
              labelText: '可使用次數',
              hintText: '例如 3',
              prefixIcon: Icons.confirmation_number_outlined,
              keyboardType: TextInputType.number,
              inputFormatters: [FilteringTextInputFormatter.digitsOnly],
              enabled: !_isGenerating,
              validator: (value) {
                final n = int.tryParse(value ?? '');
                if (n == null || n <= 0) return '請輸入大於 0 的次數';
                return null;
              },
            ),
            const SizedBox(height: AppSpacing.lg),
            AppPrimaryButton(
              label: '產生訪客授權',
              icon: Icons.qr_code_2,
              isLoading: _isGenerating,
              onPressed: _generate,
            ),
          ],
        ),
      ),
    ];
  }

  List<Widget> _buildResult(Map<String, dynamic> data) {
    final guestUid = asText(data['guest_uid'] ?? data['user_id']);
    final password = asText(data['password'] ?? data['guest_password']);
    final controlUrl = asText(data['control_url'], fallback: '');

    // QR 內容：後端給了 control_url 就用它，否則退回帳密資訊，
    // 至少讓訪客能掃到憑證而不是一個壞掉的網址。
    final qrData = controlUrl.isNotEmpty
        ? controlUrl
        : 'iota-guest://login?uid=$guestUid&pwd=$password';

    final isPlaceholderUrl = controlUrl.contains('your-domain.com');

    return [
      Container(
        padding: const EdgeInsets.all(AppSpacing.md),
        decoration: BoxDecoration(
          color: AppColors.warning.withValues(alpha: 0.12),
          borderRadius: BorderRadius.circular(AppRadius.md),
          border: Border.all(color: AppColors.warning.withValues(alpha: 0.4)),
        ),
        child: const Text(
          '⚠️ 訪客密碼只會顯示這一次，離開此畫面後無法再查詢。'
          '請立即提供給訪客，或截圖保存。',
          style: TextStyle(
              color: AppColors.warning, fontSize: 12, height: 1.5),
        ),
      ),
      const SizedBox(height: AppSpacing.lg),
      Center(
        child: Container(
          padding: const EdgeInsets.all(AppSpacing.md),
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(AppRadius.lg),
          ),
          child: QrImageView(
            data: qrData,
            version: QrVersions.auto,
            size: 220,
            backgroundColor: Colors.white,
          ),
        ),
      ),
      if (isPlaceholderUrl) ...[
        const SizedBox(height: AppSpacing.md),
        Container(
          padding: const EdgeInsets.all(AppSpacing.md),
          decoration: BoxDecoration(
            color: AppColors.danger.withValues(alpha: 0.12),
            borderRadius: BorderRadius.circular(AppRadius.md),
            border: Border.all(color: AppColors.danger.withValues(alpha: 0.4)),
          ),
          child: Text(
            '注意：後端回傳的控制網址仍是佔位網域\n$controlUrl\n'
            '掃描這個 QR 不會連到真實服務。正式部署前需要在 '
            'generate_guest_qr.py 設定真實網域。',
            style: const TextStyle(
                color: AppColors.dangerLight, fontSize: 11, height: 1.5),
          ),
        ),
      ],
      const SizedBox(height: AppSpacing.lg),
      _credentialRow('訪客帳號', guestUid),
      _credentialRow('訪客密碼', password),
      // 以後端實際核發的值為準，後端沒回才退回本機送出的值。
      // 舊版直接顯示 `_endTime` 與輸入框文字，後端若有夾限（例如上限 24 小時）
      // 畫面會顯示一個根本沒生效的期限，訪客到時候才發現進不去。
      _credentialRow(
        '有效期限',
        formatDisplay(data['end_time'] ?? data['expires_at'],
            fallback: formatDisplay(_endTime)),
      ),
      _credentialRow(
        '可使用次數',
        asText(data['max_uses'], fallback: _maxUsesController.text),
      ),
      const SizedBox(height: AppSpacing.lg),
      OutlinedButton.icon(
        onPressed: () => setState(() => _result = null),
        icon: const Icon(Icons.refresh),
        label: const Text('再產生一組'),
        style: OutlinedButton.styleFrom(
          foregroundColor: AppColors.indigoLight,
          side: const BorderSide(color: AppColors.indigo),
          padding: const EdgeInsets.symmetric(vertical: 14),
        ),
      ),
    ];
  }

  Widget _credentialRow(String label, String value) {
    return Container(
      margin: const EdgeInsets.only(bottom: AppSpacing.sm),
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.6),
        borderRadius: BorderRadius.circular(AppRadius.md),
        border: Border.all(color: AppColors.border),
      ),
      child: Row(
        children: [
          SizedBox(
            width: 90,
            child: Text(label,
                style: TextStyle(
                    color: AppColors.textTertiary, fontSize: 13)),
          ),
          Expanded(
            child: SelectableText(
              value,
              style: const TextStyle(
                color: AppColors.textPrimary,
                fontSize: 14,
                fontWeight: FontWeight.bold,
                fontFamily: 'monospace',
              ),
            ),
          ),
          IconButton(
            icon: Icon(Icons.copy, size: 18, color: AppColors.textTertiary),
            tooltip: '複製',
            onPressed: () async {
              await Clipboard.setData(ClipboardData(text: value));
              if (mounted) AppFeedback.success(context, '已複製 $label');
            },
          ),
        ],
      ),
    );
  }
}
