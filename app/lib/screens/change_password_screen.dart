import 'dart:convert';

import 'package:flutter/material.dart';

import '../theme/app_theme.dart';
import '../utils/api_client.dart';
import '../utils/api_config.dart';
import '../utils/app_feedback.dart';
import '../utils/session.dart';
import '../widgets/app_text_field.dart';
import 'family_list_screen.dart';
import 'login_screen.dart';

/// 修改自己的密碼（`/change_password`）。
///
/// 後端沒有 Token，身分只靠 payload 的 user_id，所以一定要驗舊密碼。
/// 本機驗證規則跟後端一致（至少 8 字元、bcrypt 上限 72 bytes、不可與舊密碼相同），
/// 只是讓使用者早點看到錯誤；最終仍以後端回應為準。
///
/// [forced] 為 true 時是「管理員重設後第一次登入」：舊密碼就是臨時密碼，
/// 不能返回，改完直接進主畫面；也提供改用其他帳號登入，避免被困在這頁。
class ChangePasswordScreen extends StatefulWidget {
  const ChangePasswordScreen({
    super.key,
    required this.userId,
    this.forced = false,
  });

  final String userId;
  final bool forced;

  @override
  State<ChangePasswordScreen> createState() => _ChangePasswordScreenState();
}

class _ChangePasswordScreenState extends State<ChangePasswordScreen> {
  static const int _minLength = 8;
  static const int _maxBytes = 72;

  final _formKey = GlobalKey<FormState>();
  final _oldController = TextEditingController();
  final _newController = TextEditingController();
  final _confirmController = TextEditingController();

  bool _isSubmitting = false;

  @override
  void dispose() {
    _oldController.dispose();
    _newController.dispose();
    _confirmController.dispose();
    super.dispose();
  }

  String? _validateNew(String? value) {
    final v = value ?? '';
    if (v.length < _minLength) return '新密碼至少需要 $_minLength 個字元';
    if (utf8.encode(v).length > _maxBytes) return '新密碼過長（上限 $_maxBytes bytes）';
    if (v == _oldController.text) return '新密碼不可與舊密碼相同';
    return null;
  }

  Future<void> _submit() async {
    if (_isSubmitting) return;
    if (!(_formKey.currentState?.validate() ?? false)) return;

    setState(() => _isSubmitting = true);

    final result = await ApiClient.post(ApiEndpoints.changePassword, {
      'user_id': widget.userId,
      'old_password': _oldController.text,
      'new_password': _newController.text,
    });

    if (!mounted) return;
    setState(() => _isSubmitting = false);

    if (result.ok) {
      await Session.markPasswordChanged();
      if (!mounted) return;
      AppFeedback.success(context, '密碼已更新，下次登入請使用新密碼');
      if (widget.forced && Session.current != null) {
        Navigator.pushReplacement(
          context,
          MaterialPageRoute(
            builder: (_) => FamilyListScreen(userData: Session.current!),
          ),
        );
      } else {
        Navigator.of(context).pop();
      }
    } else if (result.statusCode == 401) {
      AppFeedback.error(context, widget.forced ? '臨時密碼不正確' : '舊密碼不正確');
    } else {
      AppFeedback.fromResult(context, result);
    }
  }

  Future<void> _switchAccount() async {
    await Session.clear();
    if (!mounted) return;
    Navigator.pushAndRemoveUntil(
      context,
      MaterialPageRoute(builder: (_) => const LoginScreen()),
      (route) => false,
    );
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: !widget.forced,
      child: Scaffold(
        backgroundColor: AppColors.background,
        appBar: AppBar(
          title: Text(widget.forced ? '設定新密碼' : '修改密碼'),
          automaticallyImplyLeading: !widget.forced,
        ),
        body: ListView(
          padding: const EdgeInsets.all(AppSpacing.lg),
          children: [
            if (widget.forced) ...[
              Container(
                padding: const EdgeInsets.all(AppSpacing.md),
                decoration: BoxDecoration(
                  color: AppColors.warning.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(AppRadius.md),
                  border:
                      Border.all(color: AppColors.warning.withValues(alpha: 0.4)),
                ),
                child: const Text(
                  '你的密碼已由場域管理員重設。請輸入拿到的臨時密碼，並設定一組只有你知道的新密碼，'
                  '完成後才能繼續使用。',
                  style: TextStyle(
                      color: AppColors.warning, fontSize: 12, height: 1.5),
                ),
              ),
              const SizedBox(height: AppSpacing.lg),
            ],
            Text(
              '帳號：${widget.userId}',
              style: TextStyle(color: AppColors.textTertiary, fontSize: 13),
            ),
            const SizedBox(height: AppSpacing.lg),
            Form(
              key: _formKey,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AppTextField(
                    controller: _oldController,
                    labelText: widget.forced ? '臨時密碼' : '目前的密碼',
                    hintText:
                        widget.forced ? '輸入管理員給你的臨時密碼' : '輸入目前使用的密碼',
                    prefixIcon: Icons.lock_outline,
                    obscureText: true,
                    enabled: !_isSubmitting,
                    textInputAction: TextInputAction.next,
                    validator: (value) => (value ?? '').isEmpty
                        ? (widget.forced ? '請輸入臨時密碼' : '請輸入目前的密碼')
                        : null,
                  ),
                  const SizedBox(height: AppSpacing.md),
                  AppTextField(
                    controller: _newController,
                    labelText: '新密碼',
                    hintText: '至少 $_minLength 個字元',
                    prefixIcon: Icons.lock_reset,
                    obscureText: true,
                    enabled: !_isSubmitting,
                    textInputAction: TextInputAction.next,
                    validator: _validateNew,
                  ),
                  const SizedBox(height: AppSpacing.md),
                  AppTextField(
                    controller: _confirmController,
                    labelText: '確認新密碼',
                    hintText: '再輸入一次新密碼',
                    prefixIcon: Icons.lock_reset,
                    obscureText: true,
                    enabled: !_isSubmitting,
                    textInputAction: TextInputAction.done,
                    onFieldSubmitted: (_) => _submit(),
                    validator: (value) =>
                        value != _newController.text ? '兩次輸入的新密碼不一致' : null,
                  ),
                  const SizedBox(height: AppSpacing.lg),
                  AppPrimaryButton(
                    label: '更新密碼',
                    icon: Icons.check,
                    isLoading: _isSubmitting,
                    onPressed: _submit,
                  ),
                  if (widget.forced) ...[
                    const SizedBox(height: AppSpacing.md),
                    TextButton(
                      onPressed: _isSubmitting ? null : _switchAccount,
                      child: Text('改用其他帳號登入',
                          style: TextStyle(color: AppColors.textTertiary)),
                    ),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
