import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:qr_flutter/qr_flutter.dart';

import '../theme/app_theme.dart';
import '../utils/api_client.dart';
import '../utils/api_config.dart';
import '../utils/app_feedback.dart';
import '../utils/parsing.dart';
import '../widgets/app_text_field.dart';
import 'guest_tokens_screen.dart';

/// 訪客臨時授權 QR（UC3.4）。
///
/// 呼叫 `/generate_guest_qr`：後端核發一組 guest_token（有效期限＋次數上限），
/// 回傳訪客開門頁的 `control_url`（token 放在網址的 `#t=` 片段）與
/// `authorization_id`（撤銷時用的 token_id）。
///
/// 兩個必須讓使用者知道的限制：
///   1. 明文 token 只出現在這次回應，後端只存雜湊，離開畫面就查不回這個連結。
///   2. `control_url` 的主機預設是 App 連 API 的那個位址（或後端
///      `GUEST_QR_BASE_URL`）。若是 localhost，訪客手機一定連不到 —— 如實標示。
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

    if (!_endTime.isAfter(DateTime.now())) {
      AppFeedback.error(context, '有效期限必須晚於現在，請重新選擇');
      return;
    }

    setState(() => _isGenerating = true);

    final result = await ApiClient.post(ApiEndpoints.generateGuestQr, {
      'family_id': widget.familyId,
      'admin_uid': widget.adminUid,
      // 這支端點把不帶時區的字串當台灣時間，所以一定要送帶 Z 的 UTC，
      // 不能用 formatForApi（UTC naive 會被往前解讀 8 小時，期限直接過期）。
      'end_time': formatIsoUtc(_endTime),
      'max_uses': int.tryParse(_maxUsesController.text) ?? 1,
    });

    if (!mounted) return;
    setState(() {
      _isGenerating = false;
      if (result.ok) _result = result.dataMap;
    });

    if (!result.ok) AppFeedback.fromResult(context, result);
  }

  void _openTokenList() {
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => GuestTokensScreen(
          familyId: widget.familyId,
          familyName: widget.familyName,
          adminUid: widget.adminUid,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        title: const Text('訪客臨時授權'),
        actions: [
          IconButton(
            tooltip: '已核發的通行碼',
            icon: const Icon(Icons.manage_history_outlined),
            onPressed: _openTokenList,
          ),
        ],
      ),
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
        '為「${widget.familyName}」產生一組訪客通行碼。'
        '訪客掃描 QR 會開啟開門頁面，在期限與次數上限內可以開鎖。',
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
            const SizedBox(height: AppSpacing.md),
            _tokenListButton(),
          ],
        ),
      ),
    ];
  }

  List<Widget> _buildResult(Map<String, dynamic> data) {
    final controlUrl = asText(data['control_url'], fallback: '');
    final authorizationId = asText(data['authorization_id']);
    final maxUses = data['max_uses'];

    final host = Uri.tryParse(controlUrl)?.host ?? '';
    final isLoopback =
        host == 'localhost' || host == '127.0.0.1' || host == '10.0.2.2';

    return [
      Container(
        padding: const EdgeInsets.all(AppSpacing.md),
        decoration: BoxDecoration(
          color: AppColors.warning.withValues(alpha: 0.12),
          borderRadius: BorderRadius.circular(AppRadius.md),
          border: Border.all(color: AppColors.warning.withValues(alpha: 0.4)),
        ),
        child: const Text(
          '⚠️ 這個 QR 與開門連結只會顯示這一次，後端不保存明文，離開此畫面後無法再取得。'
          '請立即提供給訪客，或截圖保存。需要停用時，到「已核發的通行碼」撤銷。',
          style: TextStyle(
              color: AppColors.warning, fontSize: 12, height: 1.5),
        ),
      ),
      const SizedBox(height: AppSpacing.lg),
      if (controlUrl.isEmpty)
        const Text(
          '後端沒有回傳開門連結，無法產生 QR。請確認後端版本，或設定 GUEST_QR_BASE_URL。',
          style: TextStyle(color: AppColors.dangerLight, fontSize: 13),
        )
      else
        Center(
          child: Container(
            padding: const EdgeInsets.all(AppSpacing.md),
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(AppRadius.lg),
            ),
            child: QrImageView(
              data: controlUrl,
              version: QrVersions.auto,
              size: 220,
              backgroundColor: Colors.white,
            ),
          ),
        ),
      if (isLoopback) ...[
        const SizedBox(height: AppSpacing.md),
        Container(
          padding: const EdgeInsets.all(AppSpacing.md),
          decoration: BoxDecoration(
            color: AppColors.danger.withValues(alpha: 0.12),
            borderRadius: BorderRadius.circular(AppRadius.md),
            border: Border.all(color: AppColors.danger.withValues(alpha: 0.4)),
          ),
          child: Text(
            '注意：開門連結指向 $host，訪客手機連不到。'
            '請在伺服器設定改用電腦的區網 IP，或在後端 .env 設定 GUEST_QR_BASE_URL。',
            style: const TextStyle(
                color: AppColors.dangerLight, fontSize: 11, height: 1.5),
          ),
        ),
      ],
      const SizedBox(height: AppSpacing.lg),
      if (controlUrl.isNotEmpty) _infoRow('開門連結', controlUrl, copyable: true),
      _infoRow('授權編號', authorizationId, copyable: true),
      // 以後端實際核發的值為準，後端沒回才退回本機送出的值。
      // 後端若有夾限，畫面不能顯示一個根本沒生效的期限。
      _infoRow(
        '有效期限',
        formatDisplay(data['end_time'], fallback: formatDisplay(_endTime)),
      ),
      _infoRow(
        '可使用次數',
        maxUses == null ? '不限' : asText(maxUses),
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
      const SizedBox(height: AppSpacing.md),
      _tokenListButton(),
    ];
  }

  Widget _tokenListButton() {
    return TextButton.icon(
      onPressed: _openTokenList,
      icon: const Icon(Icons.manage_history_outlined, size: 18),
      label: const Text('查看或撤銷已核發的通行碼'),
      style: TextButton.styleFrom(foregroundColor: AppColors.indigoLight),
    );
  }

  Widget _infoRow(String label, String value, {bool copyable = false}) {
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
              maxLines: 3,
              style: const TextStyle(
                color: AppColors.textPrimary,
                fontSize: 14,
                fontWeight: FontWeight.bold,
                fontFamily: 'monospace',
              ),
            ),
          ),
          if (copyable)
            IconButton(
              icon: Icon(Icons.copy, size: 18, color: AppColors.textTertiary),
              tooltip: '複製',
              onPressed: () async {
                await Clipboard.setData(ClipboardData(text: value));
                if (mounted) AppFeedback.success(context, '已複製$label');
              },
            ),
        ],
      ),
    );
  }
}
