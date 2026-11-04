import 'package:flutter/material.dart';

import '../theme/app_theme.dart';
import '../utils/api_client.dart';
import '../utils/api_config.dart';
import '../utils/app_feedback.dart';
import '../utils/parsing.dart';
import '../widgets/empty_state.dart';

/// 已核發的訪客通行碼清單與撤銷（UC3.5）。
///
/// `/revoke_guest_token` 要 token_id，但產生 QR 時只拿到一次
/// `authorization_id`，之後只能從 `/list_guest_tokens` 查回來。
/// 兩支都限場域 Admin；清單不回明文 token，也不寫稽核日誌（可以放心重新整理）。
class GuestTokensScreen extends StatefulWidget {
  const GuestTokensScreen({
    super.key,
    required this.familyId,
    required this.familyName,
    required this.adminUid,
  });

  final int familyId;
  final String familyName;
  final String adminUid;

  @override
  State<GuestTokensScreen> createState() => _GuestTokensScreenState();
}

class _GuestTokensScreenState extends State<GuestTokensScreen> {
  List<Map<String, dynamic>> _tokens = const [];
  bool _isLoading = true;
  bool _showInactive = false;
  String? _error;

  /// 正在撤銷的 token_id，用來鎖住那一張卡片的按鈕。
  String? _revokingId;

  @override
  void initState() {
    super.initState();
    _fetch();
  }

  Future<void> _fetch() async {
    setState(() {
      _isLoading = true;
      _error = null;
    });

    final result = await ApiClient.post(ApiEndpoints.listGuestTokens, {
      'family_id': widget.familyId,
      'admin_uid': widget.adminUid,
      'include_inactive': _showInactive,
    });

    if (!mounted) return;
    setState(() {
      _isLoading = false;
      if (result.ok) {
        _tokens = result.dataList.whereType<Map<String, dynamic>>().toList();
      } else {
        _error = result.message;
      }
    });
  }

  Future<void> _confirmRevoke(Map<String, dynamic> token) async {
    final tokenId = asText(token['token_id'], fallback: '');
    if (tokenId.isEmpty) return;

    final reasonController = TextEditingController();
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('撤銷這組通行碼？'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '撤銷後訪客立即無法再開門，這個動作無法復原，並會寫入稽核紀錄。',
              style: TextStyle(color: AppColors.textSecondary, height: 1.5),
            ),
            const SizedBox(height: AppSpacing.md),
            TextField(
              controller: reasonController,
              maxLength: 100,
              decoration: const InputDecoration(
                labelText: '原因（選填）',
                hintText: '例如：訪客已離開',
              ),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: Text('取消', style: TextStyle(color: AppColors.textTertiary)),
          ),
          ElevatedButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            style: ElevatedButton.styleFrom(
              backgroundColor: AppColors.danger,
              foregroundColor: Colors.white,
            ),
            child: const Text('確定撤銷'),
          ),
        ],
      ),
    );
    final reason = reasonController.text.trim();
    reasonController.dispose();

    if (confirmed != true || !mounted) return;
    await _revoke(tokenId, reason);
  }

  Future<void> _revoke(String tokenId, String reason) async {
    setState(() => _revokingId = tokenId);

    final result = await ApiClient.post(ApiEndpoints.revokeGuestToken, {
      'token_id': tokenId,
      'operator_user_id': widget.adminUid,
      'family_id': widget.familyId,
      if (reason.isNotEmpty) 'reason': reason,
    });

    if (!mounted) return;
    setState(() => _revokingId = null);

    if (result.ok) {
      AppFeedback.success(context, '已撤銷通行碼');
    } else if (result.statusCode == 409) {
      // 已自然過期或次數用完：後端不另外建立撤銷事件，清單重新整理就會消失。
      AppFeedback.warning(context, '這組通行碼已經過期或次數用完，不需要撤銷');
    } else {
      AppFeedback.fromResult(context, result);
    }
    await _fetch();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        title: const Text('已核發的通行碼'),
        actions: [
          IconButton(
            tooltip: '重新整理',
            icon: const Icon(Icons.refresh),
            onPressed: _isLoading ? null : _fetch,
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _fetch,
        child: ListView(
          padding: const EdgeInsets.all(AppSpacing.lg),
          children: [
            Text(
              '「${widget.familyName}」核發過的訪客通行碼。'
              '只有生效中與尚未生效的可以撤銷。',
              style: TextStyle(
                  color: AppColors.textTertiary, fontSize: 13, height: 1.5),
            ),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: Text('顯示已失效的通行碼',
                  style:
                      TextStyle(color: AppColors.textSecondary, fontSize: 14)),
              subtitle: Text('已撤銷、已過期、次數已用完',
                  style:
                      TextStyle(color: AppColors.textTertiary, fontSize: 12)),
              value: _showInactive,
              activeThumbColor: AppColors.purple,
              onChanged: _isLoading
                  ? null
                  : (value) {
                      setState(() => _showInactive = value);
                      _fetch();
                    },
            ),
            const SizedBox(height: AppSpacing.sm),
            ..._buildBody(),
          ],
        ),
      ),
    );
  }

  List<Widget> _buildBody() {
    if (_isLoading) {
      return const [
        Padding(
          padding: EdgeInsets.only(top: 48),
          child: Center(child: CircularProgressIndicator()),
        ),
      ];
    }
    if (_error != null) {
      return [
        EmptyState(
          icon: Icons.error_outline,
          title: '無法載入通行碼',
          subtitle: _error,
          isError: true,
          action: OutlinedButton(onPressed: _fetch, child: const Text('重試')),
        ),
      ];
    }
    if (_tokens.isEmpty) {
      return [
        EmptyState(
          icon: Icons.qr_code_2,
          title: _showInactive ? '還沒有核發過通行碼' : '目前沒有生效中的通行碼',
          subtitle: '在「訪客臨時授權」產生 QR 後，會出現在這裡。',
        ),
      ];
    }
    return _tokens.map(_buildTokenCard).toList();
  }

  Widget _buildTokenCard(Map<String, dynamic> token) {
    final tokenId = asText(token['token_id']);
    final status = asText(token['status'], fallback: '');
    final style = _statusStyle(status);
    final canRevoke = status == 'ACTIVE' || status == 'PENDING';
    final isRevoking = _revokingId == tokenId;

    final deviceName = asText(token['device_name'], fallback: '');
    final deviceId = asText(token['device_id'], fallback: '');
    final deviceLabel = deviceName.isNotEmpty
        ? deviceName
        : (deviceId.isNotEmpty ? deviceId : '場域內所有裝置');

    final maxUses = token['max_uses'];
    final usage = maxUses == null
        ? '已使用 ${asText(token['used_count'], fallback: '0')} 次（不限次數）'
        : '已使用 ${asText(token['used_count'], fallback: '0')} / ${asText(maxUses)} 次';

    return Container(
      margin: const EdgeInsets.only(bottom: AppSpacing.md),
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.6),
        borderRadius: BorderRadius.circular(AppRadius.lg),
        border: Border.all(color: AppColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(
                  color: style.color.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(AppRadius.sm),
                ),
                child: Text(style.label,
                    style: TextStyle(
                        color: style.color,
                        fontSize: 12,
                        fontWeight: FontWeight.bold)),
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Text(
                  deviceLabel,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                      color: AppColors.textPrimary,
                      fontSize: 15,
                      fontWeight: FontWeight.bold),
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          _line(Icons.event_busy_outlined,
              '期限 ${formatDisplay(token['expires_at'])}'),
          if (token['valid_from'] != null && status == 'PENDING')
            _line(Icons.schedule,
                '生效時間 ${formatDisplay(token['valid_from'])}'),
          _line(Icons.confirmation_number_outlined, usage),
          _line(Icons.person_outline,
              '由 ${asText(token['created_by'])} 於 ${formatDisplay(token['created_at'])} 核發'),
          if (status == 'REVOKED')
            _line(Icons.block,
                '已於 ${formatDisplay(token['revoked_at'])} 由 ${asText(token['revoked_by'])} 撤銷'),
          _line(Icons.tag, tokenId, mono: true),
          if (canRevoke) ...[
            const SizedBox(height: AppSpacing.sm),
            Align(
              alignment: Alignment.centerRight,
              child: OutlinedButton.icon(
                onPressed: isRevoking || _revokingId != null
                    ? null
                    : () => _confirmRevoke(token),
                icon: isRevoking
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.block, size: 18),
                label: Text(isRevoking ? '撤銷中…' : '撤銷'),
                style: OutlinedButton.styleFrom(
                  foregroundColor: AppColors.dangerLight,
                  side: BorderSide(
                      color: AppColors.danger.withValues(alpha: 0.6)),
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _line(IconData icon, String text, {bool mono = false}) {
    return Padding(
      padding: const EdgeInsets.only(top: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 15, color: AppColors.textTertiary),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              text,
              style: TextStyle(
                color: AppColors.textSecondary,
                fontSize: mono ? 11 : 13,
                fontFamily: mono ? 'monospace' : null,
              ),
            ),
          ),
        ],
      ),
    );
  }

  ({String label, Color color}) _statusStyle(String status) {
    switch (status) {
      case 'ACTIVE':
        return (label: '生效中', color: AppColors.success);
      case 'PENDING':
        return (label: '尚未生效', color: AppColors.info);
      case 'EXPIRED':
        return (label: '已過期', color: AppColors.textTertiary);
      case 'USED_UP':
        return (label: '次數用完', color: AppColors.textTertiary);
      case 'REVOKED':
        return (label: '已撤銷', color: AppColors.danger);
      default:
        return (label: status.isEmpty ? '未知' : status, color: AppColors.textTertiary);
    }
  }
}
