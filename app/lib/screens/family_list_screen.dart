import 'package:flutter/material.dart';

import '../theme/app_theme.dart';
import '../utils/api_client.dart';
import '../utils/api_config.dart';
import '../utils/app_feedback.dart';
import '../utils/parsing.dart';
import '../utils/session.dart';
import '../widgets/app_text_field.dart';
import '../widgets/empty_state.dart';
import 'family_detail_screen.dart';
import 'login_screen.dart';
import 'server_settings_screen.dart';

/// 使用者的場域（家庭）清單與邀請通知。
///
/// 這支檔案原本叫 `dashboard_screen.dart`，但它顯示的是「我加入了哪些場域」，
/// 跟 UC4.3 的場域儀表板（`/dashboard`，裝置狀態與連線健康度）是兩件事 ——
/// 舊名稱會讓人以為儀表板已經做好了。真正的儀表板在 `dashboard_screen.dart`。
class FamilyListScreen extends StatefulWidget {
  const FamilyListScreen({super.key, required this.userData});

  /// `/login` 回傳的 `data`：user_id / username / status / families。
  final Map<String, dynamic> userData;

  @override
  State<FamilyListScreen> createState() => _FamilyListScreenState();
}

class _FamilyListScreenState extends State<FamilyListScreen> {
  List<dynamic> _families = [];
  List<dynamic> _invitations = [];
  bool _isLoadingFamilies = false;
  bool _isLoadingInvitations = false;
  bool _isCreatingFamily = false;

  /// 家庭清單載入失敗時的訊息。舊版只 debugPrint，使用者完全不知道看到的是
  /// 登入當下的舊快照，還以為是最新資料。
  String? _familiesError;

  /// 邀請清單載入失敗時的訊息。
  String? _invitationsError;

  int _activeTab = 0;

  String get _userId => (widget.userData['user_id'] ?? '').toString();

  @override
  void initState() {
    super.initState();
    // 先用登入回應裡的清單墊著，避免進來先閃一次空狀態。
    _families = widget.userData['families'] as List<dynamic>? ?? [];
    _refreshAll();
  }

  Future<void> _refreshAll() async {
    await Future.wait([_fetchFamilies(), _fetchInvitations()]);
  }

  Future<void> _fetchFamilies() async {
    if (mounted) setState(() => _isLoadingFamilies = true);

    final result = await ApiClient.post(
      ApiEndpoints.getUserFamilies,
      {'user_id': _userId},
    );

    // 舊版在 finally 裡無條件 setState，使用者在請求飛行中返回就會拋例外。
    if (!mounted) return;
    setState(() {
      _isLoadingFamilies = false;
      if (result.ok) {
        _families = result.dataList;
        _familiesError = null;
      } else {
        _familiesError = result.message;
      }
    });
  }

  Future<void> _fetchInvitations() async {
    if (mounted) setState(() => _isLoadingInvitations = true);

    final result = await ApiClient.post(
      ApiEndpoints.getInvitations,
      {'user_id': _userId},
    );

    if (!mounted) return;
    setState(() {
      _isLoadingInvitations = false;
      if (result.ok) {
        // 只留待處理的。列表上的「接受 / 拒絕」按鈕對已處理的邀請沒有意義，
        // 而且分頁徽章本來就只算 Pending，兩邊的認定必須一致。
        _invitations = result.dataList
            .where((inv) =>
                inv is Map &&
                asText(inv['status'], fallback: 'Pending').toLowerCase() ==
                    'pending')
            .toList();
        _invitationsError = null;
      } else {
        // 舊版失敗時完全靜音，畫面照樣顯示「目前沒有任何待確認的邀請」——
        // 有邀請卻說沒有，比直接報錯更糟。
        _invitationsError = result.message;
      }
    });
  }

  Future<void> _respondToInvitation(int invitationId, String action) async {
    final result = await ApiClient.post(ApiEndpoints.respondInvitation, {
      'invitation_id': invitationId,
      'user_id': _userId,
      'action': action,
    });

    if (!mounted) return;
    AppFeedback.fromResult(context, result);
    if (result.ok) await _refreshAll();
  }

  Future<void> _showCreateFamilyDialog() async {
    final controller = TextEditingController();
    final formKey = GlobalKey<FormState>();

    try {
      final name = await showDialog<String>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          title: const Text('建立新場域'),
          content: Form(
            key: formKey,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '建立後您將成為這個場域的 Admin，可以配對裝置與邀請成員。',
                  style: TextStyle(color: AppColors.textTertiary, fontSize: 13),
                ),
                const SizedBox(height: AppSpacing.md),
                AppTextField(
                  controller: controller,
                  labelText: '場域名稱',
                  hintText: '例如：鶯歌老家、台北租屋處',
                  prefixIcon: Icons.home_outlined,
                  textInputAction: TextInputAction.done,
                  validator: (value) => (value == null || value.trim().isEmpty)
                      ? '請輸入場域名稱'
                      : null,
                ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogContext).pop(),
              child:
                  Text('取消', style: TextStyle(color: AppColors.textTertiary)),
            ),
            ElevatedButton(
              onPressed: () {
                if (formKey.currentState?.validate() ?? false) {
                  Navigator.of(dialogContext).pop(controller.text.trim());
                }
              },
              style: ElevatedButton.styleFrom(
                backgroundColor: AppColors.indigo,
                foregroundColor: Colors.white,
              ),
              child: const Text('建立'),
            ),
          ],
        ),
      );

      if (name == null || name.isEmpty || !mounted) return;

      setState(() => _isCreatingFamily = true);
      final result = await ApiClient.post(ApiEndpoints.createFamily, {
        'user_id': _userId,
        'family_name': name,
      });

      if (!mounted) return;
      setState(() => _isCreatingFamily = false);
      AppFeedback.fromResult(context, result);
      if (result.ok) await _fetchFamilies();
    } finally {
      // 對話框裡 new 出來的 controller 必須自己釋放；舊程式碼在多處遺漏，
      // 每開一次對話框就洩漏一個 TextEditingController。
      controller.dispose();
    }
  }

  void _handleLogout() {
    showDialog<void>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('確定登出？'),
        content: Text(
          '您需要重新輸入帳號與密碼才能存取主控台。',
          style: TextStyle(color: AppColors.textSecondary),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(),
            child: Text('取消', style: TextStyle(color: AppColors.textTertiary)),
          ),
          ElevatedButton(
            onPressed: () async {
              Navigator.of(dialogContext).pop();
              // 清掉本機保存的身分，否則下次開 App 會自動登入回來。
              await Session.clear();
              if (!mounted) return;
              Navigator.pushAndRemoveUntil(
                context,
                MaterialPageRoute(builder: (_) => const LoginScreen()),
                (route) => false,
              );
            },
            style: ElevatedButton.styleFrom(
              backgroundColor: AppColors.danger,
              foregroundColor: Colors.white,
            ),
            child: const Text('確定登出'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _isCreatingFamily ? null : _showCreateFamilyDialog,
        backgroundColor: AppColors.indigo,
        foregroundColor: Colors.white,
        icon: _isCreatingFamily
            ? const SizedBox(
                width: 18,
                height: 18,
                child: CircularProgressIndicator(
                    strokeWidth: 2, color: Colors.white),
              )
            : const Icon(Icons.add_home_outlined),
        label: const Text('建立場域'),
      ),
      body: Stack(
        children: [
          const Positioned(
            top: -120,
            right: -80,
            child: GlowOrb(color: AppColors.indigo, diameter: 300),
          ),
          const Positioned(
            bottom: -100,
            left: -80,
            child: GlowOrb(
              color: AppColors.pink,
              diameter: 350,
              opacity: 0.08,
              blur: 120,
              spread: 60,
            ),
          ),
          SafeArea(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _buildHeader(),
                _buildTabSwitcher(),
                Expanded(
                  child: RefreshIndicator(
                    onRefresh: _refreshAll,
                    color: AppColors.purple,
                    backgroundColor: AppColors.surface,
                    child: _activeTab == 0
                        ? _buildFamiliesTab()
                        : _buildInvitationsTab(),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildHeader() {
    final username = (widget.userData['username'] ?? '').toString();

    return Container(
      padding: const EdgeInsets.all(AppSpacing.lg),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.4),
        border: Border(bottom: BorderSide(color: AppColors.border)),
      ),
      child: Row(
        children: [
          Container(
            width: 54,
            height: 54,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              gradient: AppColors.accentGradient,
              boxShadow: [
                BoxShadow(
                  color: AppColors.purple.withValues(alpha: 0.3),
                  blurRadius: 8,
                  offset: const Offset(0, 3),
                ),
              ],
            ),
            child: Center(
              child: Text(
                // 舊版是 username.toString().substring(0, 1)：username 為空字串
                // 時直接 RangeError 讓整頁崩掉。
                initialOf(username),
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 22,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ),
          ),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('歡迎回來 👋',
                    style:
                        TextStyle(color: AppColors.textTertiary, fontSize: 13)),
                const SizedBox(height: AppSpacing.xs),
                Text(
                  username.isEmpty ? '使用者' : username,
                  style: const TextStyle(
                    color: AppColors.textPrimary,
                    fontSize: 20,
                    fontWeight: FontWeight.bold,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 2),
                Row(
                  children: [
                    _chip('ID: $_userId', AppColors.textSecondary,
                        Colors.white.withValues(alpha: 0.08)),
                    const SizedBox(width: AppSpacing.sm),
                    _chip(
                      (widget.userData['status'] ?? 'Active').toString(),
                      AppColors.indigoLight,
                      AppColors.indigo.withValues(alpha: 0.2),
                    ),
                  ],
                ),
              ],
            ),
          ),
          IconButton(
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const ServerSettingsScreen()),
            ),
            icon: Icon(Icons.dns_outlined, color: AppColors.textTertiary),
            tooltip: '伺服器設定',
          ),
          IconButton(
            onPressed: _handleLogout,
            icon: const Icon(Icons.logout_rounded,
                color: AppColors.danger, size: 24),
            tooltip: '登出系統',
          ),
        ],
      ),
    );
  }

  Widget _chip(String text, Color fg, Color bg) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.circular(4),
        ),
        child: Text(
          text,
          style: TextStyle(
            color: fg,
            fontSize: 11,
            fontWeight: FontWeight.bold,
            fontFamily: 'monospace',
          ),
        ),
      );

  Widget _buildTabSwitcher() {
    // _fetchInvitations 已經只保留 Pending，這裡不用再過濾一次。
    final pendingCount = _invitations.length;

    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.lg,
        vertical: AppSpacing.md,
      ),
      child: Container(
        height: 52,
        decoration: BoxDecoration(
          color: AppColors.surface.withValues(alpha: 0.6),
          borderRadius: BorderRadius.circular(AppRadius.lg),
          border: Border.all(color: AppColors.border),
        ),
        child: Row(
          children: [
            Expanded(
              child: _tabItem(0, '我的場域', Icons.home_work_outlined),
            ),
            Expanded(
              child: _tabItem(
                1,
                pendingCount > 0 ? '邀請通知 ($pendingCount)' : '邀請通知',
                Icons.mail_outline_rounded,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _tabItem(int index, String title, IconData icon) {
    final isActive = _activeTab == index;
    return GestureDetector(
      onTap: () => setState(() => _activeTab = index),
      child: Container(
        margin: const EdgeInsets.all(AppSpacing.xs),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(AppRadius.md),
          gradient: isActive ? AppColors.accentGradient : null,
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon,
                color: isActive ? Colors.white : AppColors.textDisabled,
                size: 18),
            const SizedBox(width: AppSpacing.sm),
            Flexible(
              child: Text(
                title,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  color: isActive ? Colors.white : AppColors.textTertiary,
                  fontWeight: isActive ? FontWeight.bold : FontWeight.normal,
                  fontSize: 14,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildFamiliesTab() {
    if (_isLoadingFamilies && _families.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }

    if (_families.isEmpty) {
      return EmptyState(
        icon: Icons.home_outlined,
        title: '目前沒有加入任何場域',
        subtitle: _familiesError ??
            '點右下角「建立場域」開始，或請家庭管理員發送邀請給您。',
        isError: _familiesError != null,
      );
    }

    return ListView.builder(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.fromLTRB(
          AppSpacing.lg, 0, AppSpacing.lg, 96),
      itemCount: _families.length + (_familiesError != null ? 1 : 0),
      itemBuilder: (context, index) {
        if (_familiesError != null && index == 0) {
          return _buildStaleBanner();
        }
        final fam = _families[index - (_familiesError != null ? 1 : 0)]
            as Map<String, dynamic>;
        return _buildFamilyCard(fam);
      },
    );
  }

  /// 刷新失敗時明確告訴使用者「這是舊資料」。
  Widget _buildStaleBanner() {
    return Container(
      margin: const EdgeInsets.only(bottom: AppSpacing.md),
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.warning.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(AppRadius.md),
        border: Border.all(color: AppColors.warning.withValues(alpha: 0.4)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.warning_amber_rounded,
              color: AppColors.warning, size: 20),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Text(
              // 自動登入時 Session 不保存 families（見 session.dart），
              // 這裡的舊資料只在「這次剛登入」才是登入快照，其餘情況是上一次
              // 成功刷新的結果。文案不寫死成「登入當時」，避免說謊。
              '無法更新場域清單，以下為先前成功載入的資料。\n$_familiesError',
              style: const TextStyle(color: AppColors.warning, fontSize: 12),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildFamilyCard(Map<String, dynamic> fam) {
    final role = (fam['user_role'] ?? 'Guest').toString();
    final isAdmin = isFamilyAdmin(role);
    // 舊版用 int.parse，後端回非數字就整頁崩。
    final familyId = asInt(fam['family_id']);
    final deviceCount = asInt(fam['device_count']);
    final memberCount = asInt(fam['member_count']);

    return Container(
      margin: const EdgeInsets.only(bottom: AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.6),
        borderRadius: BorderRadius.circular(AppRadius.xl),
        border: Border.all(color: AppColors.border),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.2),
            blurRadius: 10,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          borderRadius: BorderRadius.circular(AppRadius.xl),
          onTap: familyId == null
              ? null
              : () => Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => FamilyDetailScreen(
                        familyId: familyId,
                        familyName:
                            (fam['family_name'] ?? '未命名場域').toString(),
                        myRole: role,
                        currentUserId: _userId,
                      ),
                    ),
                  ).then((_) => _fetchFamilies()),
          child: Padding(
            padding: const EdgeInsets.all(20.0),
            child: Row(
              children: [
                Container(
                  width: 52,
                  height: 52,
                  decoration: BoxDecoration(
                    color: isAdmin
                        ? AppColors.purple.withValues(alpha: 0.15)
                        : Colors.white.withValues(alpha: 0.05),
                    borderRadius: BorderRadius.circular(AppRadius.lg),
                    border: Border.all(
                      color: isAdmin
                          ? AppColors.purple.withValues(alpha: 0.3)
                          : AppColors.border,
                    ),
                  ),
                  child: Icon(
                    Icons.home_outlined,
                    color: isAdmin
                        ? AppColors.purpleLight
                        : AppColors.textSecondary,
                    size: 26,
                  ),
                ),
                const SizedBox(width: AppSpacing.md),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        (fam['family_name'] ?? '未命名場域').toString(),
                        style: const TextStyle(
                          color: AppColors.textPrimary,
                          fontSize: 18,
                          fontWeight: FontWeight.bold,
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                      const SizedBox(height: 6),
                      Row(
                        children: [
                          Icon(Icons.shield_outlined,
                              size: 14,
                              color: isAdmin
                                  ? AppColors.indigoLight
                                  : AppColors.textDisabled),
                          const SizedBox(width: AppSpacing.xs),
                          Text(
                            roleLabel(role),
                            style: TextStyle(
                              color: isAdmin
                                  ? AppColors.indigoLight
                                  : AppColors.textSecondary,
                              fontWeight: FontWeight.bold,
                              fontSize: 13,
                            ),
                          ),
                          if (deviceCount != null) ...[
                            const SizedBox(width: AppSpacing.md - 4),
                            Icon(Icons.router_outlined,
                                size: 14, color: AppColors.textDisabled),
                            const SizedBox(width: AppSpacing.xs),
                            Text('$deviceCount',
                                style: TextStyle(
                                    color: AppColors.textTertiary,
                                    fontSize: 13)),
                          ],
                          if (memberCount != null) ...[
                            const SizedBox(width: AppSpacing.md - 4),
                            Icon(Icons.people_outline,
                                size: 14, color: AppColors.textDisabled),
                            const SizedBox(width: AppSpacing.xs),
                            Text('$memberCount',
                                style: TextStyle(
                                    color: AppColors.textTertiary,
                                    fontSize: 13)),
                          ],
                        ],
                      ),
                    ],
                  ),
                ),
                Icon(Icons.chevron_right_rounded,
                    color: AppColors.textDisabled, size: 28),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildInvitationsTab() {
    if (_isLoadingInvitations && _invitations.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }

    if (_invitations.isEmpty) {
      return EmptyState(
        icon: _invitationsError == null
            ? Icons.mark_email_read_outlined
            : Icons.cloud_off_outlined,
        title: _invitationsError == null
            ? '目前沒有任何待確認的邀請'
            : '無法載入邀請通知',
        subtitle: _invitationsError ?? '有人邀請您加入場域時，通知會出現在這裡。',
        isError: _invitationsError != null,
      );
    }

    return ListView.builder(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.fromLTRB(
          AppSpacing.lg, 0, AppSpacing.lg, 96),
      itemCount: _invitations.length,
      itemBuilder: (context, index) {
        final inv = _invitations[index] as Map<String, dynamic>;
        final invId = asInt(inv['invitation_id']);

        return Container(
          margin: const EdgeInsets.only(bottom: AppSpacing.md),
          padding: const EdgeInsets.all(20.0),
          decoration: BoxDecoration(
            color: AppColors.surface.withValues(alpha: 0.6),
            borderRadius: BorderRadius.circular(AppRadius.xl),
            border:
                Border.all(color: AppColors.indigo.withValues(alpha: 0.2)),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: AppColors.indigo.withValues(alpha: 0.1),
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(Icons.mail_rounded,
                        color: AppColors.indigoLight, size: 24),
                  ),
                  const SizedBox(width: AppSpacing.md - 4),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('加入場域邀請',
                            style: TextStyle(
                                color: AppColors.textTertiary, fontSize: 12)),
                        const SizedBox(height: AppSpacing.xs),
                        Text(
                          (inv['family_name'] ?? '未命名場域').toString(),
                          style: const TextStyle(
                            color: AppColors.textPrimary,
                            fontSize: 18,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        const SizedBox(height: AppSpacing.sm),
                        _kv('邀請人',
                            '${inv['inviter_name'] ?? '—'} (${inv['inviter_uid'] ?? '—'})'),
                        const SizedBox(height: AppSpacing.xs),
                        _kv('賦予角色', roleLabel(inv['role']),
                            valueColor: AppColors.pinkLight),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 20),
              if (invId == null)
                const Text('這筆邀請資料異常，無法操作',
                    style:
                        TextStyle(color: AppColors.danger, fontSize: 12))
              else
                Row(
                  children: [
                    Expanded(
                      child: OutlinedButton(
                        onPressed: () => _respondToInvitation(invId, 'Reject'),
                        style: OutlinedButton.styleFrom(
                          side: const BorderSide(color: AppColors.danger),
                          padding:
                              const EdgeInsets.symmetric(vertical: 12),
                          shape: RoundedRectangleBorder(
                            borderRadius:
                                BorderRadius.circular(AppRadius.md),
                          ),
                        ),
                        child: const Text('拒絕邀請',
                            style: TextStyle(
                                color: AppColors.dangerLight,
                                fontWeight: FontWeight.bold)),
                      ),
                    ),
                    const SizedBox(width: AppSpacing.md),
                    Expanded(
                      child: ElevatedButton(
                        onPressed: () => _respondToInvitation(invId, 'Accept'),
                        style: ElevatedButton.styleFrom(
                          backgroundColor: AppColors.success,
                          foregroundColor: Colors.white,
                          padding:
                              const EdgeInsets.symmetric(vertical: 12),
                          shape: RoundedRectangleBorder(
                            borderRadius:
                                BorderRadius.circular(AppRadius.md),
                          ),
                        ),
                        child: const Text('接受並加入',
                            style: TextStyle(fontWeight: FontWeight.bold)),
                      ),
                    ),
                  ],
                ),
            ],
          ),
        );
      },
    );
  }

  Widget _kv(String label, String value, {Color? valueColor}) => RichText(
        text: TextSpan(
          style: TextStyle(fontSize: 14, color: AppColors.textSecondary),
          children: [
            TextSpan(text: '$label: '),
            TextSpan(
              text: value,
              style: TextStyle(
                color: valueColor ?? AppColors.textPrimary,
                fontWeight: FontWeight.bold,
              ),
            ),
          ],
        ),
      );
}
