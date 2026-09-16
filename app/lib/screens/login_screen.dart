import 'package:flutter/material.dart';

import '../theme/app_theme.dart';
import '../utils/api_client.dart';
import '../utils/api_config.dart';
import '../utils/app_feedback.dart';
import '../utils/session.dart';
import '../widgets/app_logo.dart';
import '../widgets/app_text_field.dart';
import 'family_list_screen.dart';
import 'register_screen.dart';
import 'server_settings_screen.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen>
    with SingleTickerProviderStateMixin {
  final _formKey = GlobalKey<FormState>();
  final _userIdController = TextEditingController();
  final _passwordController = TextEditingController();

  bool _obscurePassword = true;
  bool _isLoading = false;

  late final AnimationController _animationController;
  late final Animation<double> _fadeAnimation;
  late final Animation<Offset> _slideAnimation;

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
  }

  @override
  void dispose() {
    _userIdController.dispose();
    _passwordController.dispose();
    _animationController.dispose();
    super.dispose();
  }

  Future<void> _handleLogin() async {
    if (!_formKey.currentState!.validate()) return;
    if (_isLoading) return;

    setState(() => _isLoading = true);

    final result = await ApiClient.post(ApiEndpoints.login, {
      'user_id': _userIdController.text.trim(),
      'password': _passwordController.text,
    });

    // 每次 await 之後都要重新確認畫面還在 —— 使用者可能在請求飛行中就返回了。
    if (!mounted) return;
    setState(() => _isLoading = false);

    if (!result.ok) {
      AppFeedback.fromResult(context, result);
      // 連不上後端時直接把設定入口推到眼前，比只丟一個錯誤訊息有用。
      if (result.isNetworkError) _promptServerSettings();
      return;
    }

    // 記住登入身分，下次開 App 不用重登（限制見 Session 的說明）。
    await Session.save(result.dataMap);
    if (!mounted) return;

    // 舊版登入成功會先跳一個 AlertDialog 列出家庭清單，按「好的」才進主畫面。
    // 那些資訊下一頁本來就看得到，這裡直接進去少一次點擊。
    Navigator.pushReplacement(
      context,
      MaterialPageRoute(
        builder: (_) => FamilyListScreen(userData: result.dataMap),
      ),
    );
  }

  void _promptServerSettings() {
    showDialog<void>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('連不上伺服器'),
        content: Text(
          '目前設定的位址是：\n${ApiConfig.baseUrl}\n\n'
          '請確認後端服務已啟動，或到伺服器設定調整位址。',
          style: TextStyle(color: AppColors.textSecondary),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(),
            child: Text('知道了', style: TextStyle(color: AppColors.textTertiary)),
          ),
          ElevatedButton(
            onPressed: () {
              Navigator.of(dialogContext).pop();
              _openServerSettings();
            },
            style: ElevatedButton.styleFrom(
              backgroundColor: AppColors.indigo,
              foregroundColor: Colors.white,
            ),
            child: const Text('前往設定'),
          ),
        ],
      ),
    );
  }

  Future<void> _openServerSettings() async {
    await Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => const ServerSettingsScreen()),
    );
    // 從設定頁回來後重繪，讓底部顯示的位址更新。
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      body: Stack(
        children: [
          const Positioned(
            top: -100,
            right: -100,
            child: GlowOrb(
              color: AppColors.indigo,
              diameter: 300,
              opacity: 0.15,
            ),
          ),
          const Positioned(
            bottom: -50,
            left: -100,
            child: GlowOrb(
              color: AppColors.pink,
              diameter: 350,
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
                child: Center(
                  child: SingleChildScrollView(
                    physics: const BouncingScrollPhysics(),
                    padding: const EdgeInsets.all(AppSpacing.lg),
                    child: Form(
                      key: _formKey,
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          const Center(child: AppLogo()),
                          const SizedBox(height: AppSpacing.lg),
                          const Text(
                            '歡迎回來',
                            textAlign: TextAlign.center,
                            style: TextStyle(
                              fontSize: 32,
                              fontWeight: FontWeight.bold,
                              color: AppColors.textPrimary,
                              letterSpacing: 1.5,
                            ),
                          ),
                          const SizedBox(height: AppSpacing.sm),
                          Text(
                            '請輸入您的帳號與密碼進行登入',
                            textAlign: TextAlign.center,
                            style: TextStyle(
                              fontSize: 15,
                              color: AppColors.textTertiary,
                              letterSpacing: 0.5,
                            ),
                          ),
                          const SizedBox(height: 40),
                          AppTextField(
                            controller: _userIdController,
                            labelText: '帳號名稱 / ID',
                            hintText: '請輸入您的帳號',
                            prefixIcon: Icons.badge_outlined,
                            textInputAction: TextInputAction.next,
                            enabled: !_isLoading,
                            validator: (value) =>
                                (value == null || value.trim().isEmpty)
                                    ? '請輸入帳號名稱'
                                    : null,
                          ),
                          const SizedBox(height: 20),
                          AppTextField(
                            controller: _passwordController,
                            labelText: '密碼',
                            hintText: '請輸入您的密碼',
                            prefixIcon: Icons.lock_outline,
                            obscureText: _obscurePassword,
                            textInputAction: TextInputAction.done,
                            enabled: !_isLoading,
                            onFieldSubmitted: (_) => _handleLogin(),
                            suffixIcon: IconButton(
                              icon: Icon(
                                _obscurePassword
                                    ? Icons.visibility_off_outlined
                                    : Icons.visibility_outlined,
                                color: AppColors.textTertiary,
                              ),
                              onPressed: () => setState(
                                () => _obscurePassword = !_obscurePassword,
                              ),
                            ),
                            validator: (value) =>
                                (value == null || value.isEmpty)
                                    ? '請輸入密碼'
                                    : null,
                          ),
                          const SizedBox(height: 36),
                          AppPrimaryButton(
                            label: '立即登入',
                            icon: Icons.arrow_forward_rounded,
                            isLoading: _isLoading,
                            onPressed: _handleLogin,
                          ),
                          const SizedBox(height: AppSpacing.xl),
                          Row(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              Text(
                                '還沒有帳號嗎？',
                                style: TextStyle(
                                  color: AppColors.textTertiary,
                                  fontSize: 14,
                                ),
                              ),
                              TextButton(
                                onPressed: _isLoading
                                    ? null
                                    : () => Navigator.pushReplacement(
                                          context,
                                          MaterialPageRoute(
                                            builder: (_) =>
                                                const RegisterScreen(),
                                          ),
                                        ),
                                style: TextButton.styleFrom(
                                  padding: EdgeInsets.zero,
                                  minimumSize: Size.zero,
                                  tapTargetSize:
                                      MaterialTapTargetSize.shrinkWrap,
                                ),
                                child: const Text(
                                  '立即註冊',
                                  style: TextStyle(
                                    color: AppColors.indigo,
                                    fontWeight: FontWeight.bold,
                                    fontSize: 14,
                                  ),
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: AppSpacing.md),
                          _buildServerHint(),
                        ],
                      ),
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

  /// 把目前連線的後端位址顯示在登入頁底部。
  /// 實機測試時最常見的問題就是「連錯機器」，讓它一直可見可以省下很多除錯時間。
  Widget _buildServerHint() {
    return Center(
      child: TextButton.icon(
        onPressed: _isLoading ? null : _openServerSettings,
        icon: Icon(Icons.dns_outlined, size: 14, color: AppColors.textDisabled),
        label: Text(
          ApiConfig.baseUrl,
          style: TextStyle(
            color: AppColors.textDisabled,
            fontSize: 11,
            fontFamily: 'monospace',
          ),
        ),
        style: TextButton.styleFrom(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
          minimumSize: Size.zero,
          tapTargetSize: MaterialTapTargetSize.shrinkWrap,
        ),
      ),
    );
  }
}
