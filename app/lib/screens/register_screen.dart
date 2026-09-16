import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../theme/app_theme.dart';
import '../utils/api_client.dart';
import '../utils/api_config.dart';
import '../utils/app_feedback.dart';
import '../widgets/app_logo.dart';
import '../widgets/app_text_field.dart';
import 'login_screen.dart';

class RegisterScreen extends StatefulWidget {
  const RegisterScreen({super.key});

  @override
  State<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends State<RegisterScreen>
    with SingleTickerProviderStateMixin {
  final _formKey = GlobalKey<FormState>();

  final _nameController = TextEditingController();
  final _userIdController = TextEditingController();
  final _emailController = TextEditingController();
  final _phoneController = TextEditingController();
  final _passwordController = TextEditingController();
  final _confirmPasswordController = TextEditingController();

  bool _obscurePassword = true;
  bool _obscureConfirmPassword = true;
  bool _agreeToTerms = false;
  bool _isLoading = false;

  late final AnimationController _animationController;
  late final Animation<double> _fadeAnimation;
  late final Animation<Offset> _slideAnimation;

  double _passwordStrength = 0.0;
  String _passwordStrengthText = '';
  Color _passwordStrengthColor = AppColors.healthOffline;

  @override
  void initState() {
    super.initState();

    _animationController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1000),
    );
    _fadeAnimation = Tween<double>(begin: 0.0, end: 1.0).animate(
      CurvedAnimation(
        parent: _animationController,
        curve: const Interval(0.0, 0.65, curve: Curves.easeOut),
      ),
    );
    _slideAnimation =
        Tween<Offset>(begin: const Offset(0, 0.1), end: Offset.zero).animate(
      CurvedAnimation(
        parent: _animationController,
        curve: const Interval(0.1, 0.8, curve: Curves.easeOutCubic),
      ),
    );
    _animationController.forward();

    _passwordController.addListener(_checkPasswordStrength);
  }

  @override
  void dispose() {
    _passwordController.removeListener(_checkPasswordStrength);
    _nameController.dispose();
    _userIdController.dispose();
    _emailController.dispose();
    _phoneController.dispose();
    _passwordController.dispose();
    _confirmPasswordController.dispose();
    _animationController.dispose();
    super.dispose();
  }

  void _checkPasswordStrength() {
    final password = _passwordController.text;

    if (password.isEmpty) {
      setState(() {
        _passwordStrength = 0.0;
        _passwordStrengthText = '';
        _passwordStrengthColor = AppColors.healthOffline;
      });
      return;
    }

    var strength = 0.0;
    if (password.length >= 6) strength += 0.3;
    if (password.length >= 10) strength += 0.1;
    if (password.contains(RegExp(r'[A-Z]'))) strength += 0.2;
    if (password.contains(RegExp(r'[a-z]'))) strength += 0.1;
    if (password.contains(RegExp(r'[0-9]'))) strength += 0.15;
    if (password.contains(RegExp(r'[!@#$%^&*(),.?":{}|<>]'))) strength += 0.15;

    setState(() {
      _passwordStrength = strength;
      if (strength <= 0.3) {
        _passwordStrengthText = '弱';
        _passwordStrengthColor = AppColors.danger;
      } else if (strength <= 0.7) {
        _passwordStrengthText = '中';
        _passwordStrengthColor = AppColors.warning;
      } else {
        _passwordStrengthText = '強';
        _passwordStrengthColor = AppColors.success;
      }
    });
  }

  Future<void> _handleRegister() async {
    if (!(_formKey.currentState?.validate() ?? false)) return;

    if (!_agreeToTerms) {
      AppFeedback.warning(context, '請先同意使用者條款與隱私權政策');
      return;
    }
    if (_isLoading) return;

    setState(() => _isLoading = true);

    final result = await ApiClient.post(ApiEndpoints.register, {
      'user_id': _userIdController.text.trim(),
      'username': _nameController.text.trim(),
      'password': _passwordController.text,
      'email': _emailController.text.trim(),
      'phone_number': _phoneController.text.trim(),
    });

    if (!mounted) return;
    setState(() => _isLoading = false);

    if (!result.ok) {
      AppFeedback.fromResult(context, result);
      return;
    }

    await _showSuccessDialog();
  }

  Future<void> _showSuccessDialog() async {
    if (!mounted) return;

    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) => AlertDialog(
        title: const Column(
          children: [
            Icon(Icons.check_circle_outline,
                color: AppColors.success, size: 56),
            SizedBox(height: AppSpacing.md),
            Text('註冊成功！'),
          ],
        ),
        content: Text(
          '歡迎，${_nameController.text.trim()}！\n\n'
          '登入後可以自行建立場域，或等待場域管理員邀請您加入。',
          textAlign: TextAlign.center,
          style: TextStyle(color: AppColors.textSecondary, height: 1.5),
        ),
        actionsAlignment: MainAxisAlignment.center,
        actions: [
          ElevatedButton(
            onPressed: () {
              Navigator.of(dialogContext).pop();
              Navigator.pushReplacement(
                context,
                MaterialPageRoute(builder: (_) => const LoginScreen()),
              );
            },
            style: ElevatedButton.styleFrom(
              backgroundColor: AppColors.indigo,
              foregroundColor: Colors.white,
              padding: const EdgeInsets.symmetric(
                  horizontal: AppSpacing.lg, vertical: AppSpacing.md - 4),
            ),
            child: const Text('前往登入',
                style: TextStyle(fontWeight: FontWeight.bold)),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      body: Stack(
        children: [
          const Positioned(
            top: -100,
            left: -100,
            child:
                GlowOrb(color: AppColors.purple, diameter: 300, opacity: 0.15),
          ),
          const Positioned(
            bottom: -80,
            right: -100,
            child: GlowOrb(
              color: AppColors.pink,
              diameter: 320,
              opacity: 0.12,
              blur: 120,
              spread: 60,
            ),
          ),
          SafeArea(
            child: FadeTransition(
              opacity: _fadeAnimation,
              child: SlideTransition(
                position: _slideAnimation,
                child: SingleChildScrollView(
                  physics: const BouncingScrollPhysics(),
                  padding: const EdgeInsets.all(AppSpacing.lg),
                  child: Form(
                    key: _formKey,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        const SizedBox(height: AppSpacing.md),
                        const Center(child: AppLogo(size: 64)),
                        const SizedBox(height: AppSpacing.md),
                        const Text(
                          '建立帳號',
                          textAlign: TextAlign.center,
                          style: TextStyle(
                            fontSize: 30,
                            fontWeight: FontWeight.bold,
                            color: AppColors.textPrimary,
                            letterSpacing: 1.5,
                          ),
                        ),
                        const SizedBox(height: AppSpacing.sm),
                        Text(
                          '填寫以下資訊完成註冊',
                          textAlign: TextAlign.center,
                          style: TextStyle(
                              fontSize: 15, color: AppColors.textTertiary),
                        ),
                        const SizedBox(height: AppSpacing.xl),
                        AppTextField(
                          controller: _nameController,
                          labelText: '姓名 / 暱稱',
                          hintText: '請輸入您的姓名',
                          prefixIcon: Icons.person_outline,
                          textInputAction: TextInputAction.next,
                          enabled: !_isLoading,
                          validator: (value) =>
                              (value == null || value.trim().isEmpty)
                                  ? '請輸入姓名'
                                  : null,
                        ),
                        const SizedBox(height: AppSpacing.md),
                        AppTextField(
                          controller: _userIdController,
                          labelText: '帳號名稱 / ID',
                          hintText: '英文或英數字混合，至少 4 個字元',
                          prefixIcon: Icons.badge_outlined,
                          textInputAction: TextInputAction.next,
                          enabled: !_isLoading,
                          validator: (value) {
                            final text = (value ?? '').trim();
                            if (text.isEmpty) return '請輸入帳號名稱';
                            if (text.length < 4) return '帳號長度至少需要 4 個字元';
                            if (!RegExp(r'^(?=.*[a-zA-Z])[a-zA-Z0-9]+$')
                                .hasMatch(text)) {
                              return '帳號必須為全英文或英文搭配數字（不可含中文/符號/純數字）';
                            }
                            return null;
                          },
                        ),
                        const SizedBox(height: AppSpacing.md),
                        AppTextField(
                          controller: _emailController,
                          labelText: '電子郵件',
                          hintText: 'example@mail.com',
                          prefixIcon: Icons.mail_outline,
                          keyboardType: TextInputType.emailAddress,
                          textInputAction: TextInputAction.next,
                          enabled: !_isLoading,
                          validator: (value) {
                            final text = (value ?? '').trim();
                            if (text.isEmpty) return '請輸入電子郵件';
                            if (!RegExp(r'^[\w\-.]+@([\w\-]+\.)+[\w\-]{2,}$')
                                .hasMatch(text)) {
                              return '請輸入有效的電子郵件格式';
                            }
                            return null;
                          },
                        ),
                        const SizedBox(height: AppSpacing.md),
                        AppTextField(
                          controller: _phoneController,
                          labelText: '手機號碼',
                          hintText: '09xxxxxxxx',
                          prefixIcon: Icons.phone_iphone_outlined,
                          keyboardType: TextInputType.phone,
                          textInputAction: TextInputAction.next,
                          enabled: !_isLoading,
                          inputFormatters: [
                            FilteringTextInputFormatter.digitsOnly,
                            LengthLimitingTextInputFormatter(10),
                          ],
                          validator: (value) {
                            final text = (value ?? '').trim();
                            if (text.isEmpty) return '請輸入手機號碼';
                            if (text.length < 10) return '請輸入完整的手機號碼';
                            return null;
                          },
                        ),
                        const SizedBox(height: AppSpacing.md),
                        AppTextField(
                          controller: _passwordController,
                          labelText: '密碼',
                          hintText: '至少 6 個字元',
                          prefixIcon: Icons.lock_outline,
                          obscureText: _obscurePassword,
                          textInputAction: TextInputAction.next,
                          enabled: !_isLoading,
                          suffixIcon: IconButton(
                            icon: Icon(
                              _obscurePassword
                                  ? Icons.visibility_off_outlined
                                  : Icons.visibility_outlined,
                              color: AppColors.textTertiary,
                            ),
                            onPressed: () => setState(
                                () => _obscurePassword = !_obscurePassword),
                          ),
                          validator: (value) {
                            if (value == null || value.isEmpty) {
                              return '請輸入密碼';
                            }
                            if (value.length < 6) return '密碼長度至少需要 6 個字元';
                            return null;
                          },
                        ),
                        if (_passwordStrengthText.isNotEmpty) ...[
                          const SizedBox(height: AppSpacing.sm),
                          _buildStrengthIndicator(),
                        ],
                        const SizedBox(height: AppSpacing.md),
                        AppTextField(
                          controller: _confirmPasswordController,
                          labelText: '確認密碼',
                          hintText: '請再次輸入密碼',
                          prefixIcon: Icons.lock_reset_outlined,
                          obscureText: _obscureConfirmPassword,
                          textInputAction: TextInputAction.done,
                          enabled: !_isLoading,
                          onFieldSubmitted: (_) => _handleRegister(),
                          suffixIcon: IconButton(
                            icon: Icon(
                              _obscureConfirmPassword
                                  ? Icons.visibility_off_outlined
                                  : Icons.visibility_outlined,
                              color: AppColors.textTertiary,
                            ),
                            onPressed: () => setState(() =>
                                _obscureConfirmPassword =
                                    !_obscureConfirmPassword),
                          ),
                          validator: (value) {
                            if (value == null || value.isEmpty) {
                              return '請再次輸入密碼';
                            }
                            if (value != _passwordController.text) {
                              return '密碼與確認密碼不相符';
                            }
                            return null;
                          },
                        ),
                        const SizedBox(height: AppSpacing.md),
                        _buildTermsCheckbox(),
                        const SizedBox(height: AppSpacing.lg),
                        AppPrimaryButton(
                          label: '建立帳號',
                          icon: Icons.arrow_forward_rounded,
                          isLoading: _isLoading,
                          onPressed: _handleRegister,
                        ),
                        const SizedBox(height: AppSpacing.lg),
                        Row(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            Text(
                              '已經有帳號了？',
                              style: TextStyle(
                                  color: AppColors.textTertiary, fontSize: 14),
                            ),
                            TextButton(
                              onPressed: _isLoading
                                  ? null
                                  : () => Navigator.pushReplacement(
                                        context,
                                        MaterialPageRoute(
                                          builder: (_) => const LoginScreen(),
                                        ),
                                      ),
                              style: TextButton.styleFrom(
                                padding: EdgeInsets.zero,
                                minimumSize: Size.zero,
                                tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                              ),
                              child: const Text(
                                '立即登入',
                                style: TextStyle(
                                  color: AppColors.indigo,
                                  fontWeight: FontWeight.bold,
                                  fontSize: 14,
                                ),
                              ),
                            ),
                          ],
                        ),
                        const SizedBox(height: AppSpacing.lg),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildStrengthIndicator() {
    return Row(
      children: [
        Expanded(
          child: ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
              value: _passwordStrength.clamp(0.0, 1.0),
              minHeight: 6,
              backgroundColor: Colors.white.withValues(alpha: 0.08),
              valueColor:
                  AlwaysStoppedAnimation<Color>(_passwordStrengthColor),
            ),
          ),
        ),
        const SizedBox(width: AppSpacing.sm),
        Text(
          '強度：$_passwordStrengthText',
          style: TextStyle(
            color: _passwordStrengthColor,
            fontSize: 12,
            fontWeight: FontWeight.bold,
          ),
        ),
      ],
    );
  }

  Widget _buildTermsCheckbox() {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        SizedBox(
          width: 24,
          height: 24,
          child: Checkbox(
            value: _agreeToTerms,
            activeColor: AppColors.purple,
            side: BorderSide(color: AppColors.borderStrong),
            onChanged: _isLoading
                ? null
                : (value) => setState(() => _agreeToTerms = value ?? false),
          ),
        ),
        const SizedBox(width: AppSpacing.md - 4),
        Expanded(
          child: GestureDetector(
            onTap: _isLoading
                ? null
                : () => setState(() => _agreeToTerms = !_agreeToTerms),
            child: Text(
              '我已閱讀並同意使用者條款與隱私權政策',
              style:
                  TextStyle(color: AppColors.textSecondary, fontSize: 13),
            ),
          ),
        ),
      ],
    );
  }
}
