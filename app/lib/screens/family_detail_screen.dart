import 'package:flutter/material.dart';

import '../theme/app_theme.dart';
import '../utils/api_client.dart';
import '../utils/api_config.dart';
import '../utils/app_feedback.dart';
import '../utils/parsing.dart';
import '../widgets/app_text_field.dart';
import '../widgets/device_admin_dialogs.dart';
import '../widgets/device_card.dart';
import '../widgets/device_control_sheet.dart';
import '../widgets/empty_state.dart';
import 'dashboard_screen.dart';
import 'guest_qr_screen.dart';

/// 單一場域的管理頁：成員、裝置、邀請。
class FamilyDetailScreen extends StatefulWidget {
  const FamilyDetailScreen({
    super.key,
    required this.familyId,
    required this.familyName,
    required this.myRole,
    required this.currentUserId,
  });

  final int familyId;
  final String familyName;
  final String myRole;
  final String currentUserId;

  @override
  State<FamilyDetailScreen> createState() => _FamilyDetailScreenState();
}

class _FamilyDetailScreenState extends State<FamilyDetailScreen>
    with SingleTickerProviderStateMixin {
  late final TabController _tabController;
  final _inviteFormKey = GlobalKey<FormState>();
  final _inviteeIdController = TextEditingController();

  List<dynamic> _members = [];
  List<dynamic> _devices = [];
  bool _isLoadingMembers = false;
  bool _isLoadingDevices = false;
  bool _isSendingInvite = false;
  String? _membersError;
  String? _devicesError;
  String _inviteRole = 'Guest';

  bool get _isAdmin => isFamilyAdmin(widget.myRole);

  /// Guest 會被 `control_device` 的角色檢查擋掉（只允許 admin/owner/member）。
  bool get _canControl => canControlDevices(widget.myRole);

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: _isAdmin ? 3 : 2, vsync: this);
    // build() 會讀 `_tabController.index` 決定要不要顯示「配對裝置」FAB，
    // 但切換分頁只會通知 TabBar / TabBarView，不會重建這個 Scaffold。
    // 少了這行，Admin 切到「設備狀態」時 FAB 不會出現，要等下拉重新整理之類
    // 的 setState 碰巧觸發重建才會冒出來。
    _tabController.addListener(_onTabChanged);
    _refreshAll();
  }

  void _onTabChanged() {
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    _tabController.removeListener(_onTabChanged);
    _tabController.dispose();
    _inviteeIdController.dispose();
    super.dispose();
  }

  Future<void> _refreshAll() async {
    await Future.wait([_fetchMembers(), _fetchDevices()]);
  }

  Future<void> _fetchMembers() async {
    if (mounted) setState(() => _isLoadingMembers = true);

    final result = await ApiClient.post(ApiEndpoints.getFamilyMembers, {
      'family_id': widget.familyId,
      'user_id': widget.currentUserId,
    });

    if (!mounted) return;
    setState(() {
      _isLoadingMembers = false;
      if (result.ok) {
        _members = result.dataMap['members'] as List<dynamic>? ?? [];
        _membersError = null;
      } else {
        _membersError = result.message;
      }
    });
  }

  Future<void> _fetchDevices() async {
    if (mounted) setState(() => _isLoadingDevices = true);

    // 只送 family_id，不送 user_id。
    //
    // list_devices.py 會把 user_id 當成 owner_user_id 用，SQL 變成
    // `WHERE owner_user_id = <我> AND family_id = <場域>`。裝置的 owner 是當初
    // 配對它的 Admin，所以任何 Member 進來都會看到空清單 —— 舊版就是這個 bug。
    final result = await ApiClient.post(ApiEndpoints.listDevices, {
      'family_id': widget.familyId,
    });

    if (!mounted) return;
    setState(() {
      _isLoadingDevices = false;
      if (result.ok) {
        _devices = result.dataMap['devices'] as List<dynamic>? ?? [];
        _devicesError = null;
      } else {
        _devicesError = result.message;
      }
    });
  }

  // ------------------------------------------------------------------
  // 裝置動作
  // ------------------------------------------------------------------

  Future<void> _handleDeviceAction(
    DeviceAction action,
    Map<String, dynamic> device,
  ) async {
    switch (action) {
      case DeviceAction.control:
        await _openControlSheet(device);
      case DeviceAction.maintenance:
        final changed = await DeviceAdminDialogs.maintenance(
          context: context,
          familyId: widget.familyId,
          adminUid: widget.currentUserId,
          device: device,
        );
        if (changed) await _fetchDevices();
      case DeviceAction.otaUpdate:
        await DeviceAdminDialogs.otaUpdate(
          context: context,
          familyId: widget.familyId,
          adminUid: widget.currentUserId,
          device: device,
        );
      case DeviceAction.decommission:
        final done = await DeviceAdminDialogs.decommission(
          context: context,
          operatorUserId: widget.currentUserId,
          device: device,
        );
        if (done) await _fetchDevices();
    }
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
        onStateChanged: _fetchDevices,
      ),
    );
    if (mounted) await _fetchDevices();
  }

  Future<void> _pairNewDevice() async {
    final paired = await DeviceAdminDialogs.pairDevice(
      context: context,
      familyId: widget.familyId,
      ownerUserId: widget.currentUserId,
    );
    if (paired) await _fetchDevices();
  }

  // ------------------------------------------------------------------
  // 成員動作
  // ------------------------------------------------------------------

  Future<void> _sendInvitation() async {
    if (!(_inviteFormKey.currentState?.validate() ?? false)) return;
    if (_isSendingInvite) return;

    setState(() => _isSendingInvite = true);

    final result = await ApiClient.post(ApiEndpoints.sendInvitation, {
      'family_id': widget.familyId,
      'admin_uid': widget.currentUserId,
      'invitee_uid': _inviteeIdController.text.trim(),
      'role': _inviteRole,
    });

    if (!mounted) return;
    setState(() => _isSendingInvite = false);
    AppFeedback.fromResult(context, result);

    if (result.ok) {
      _inviteeIdController.clear();
      setState(() => _inviteRole = 'Guest');
    }
  }

  Future<void> _updateMemberRole({
    required String targetUid,
    required String targetRole,
    String? startTime,
    String? endTime,
    int? maxUses,
  }) async {
    final result = await ApiClient.post(ApiEndpoints.updateMemberRole, {
      'family_id': widget.familyId,
      'admin_uid': widget.currentUserId,
      'target_uid': targetUid,
      'target_role': targetRole,
      'start_time': startTime,
      'end_time': endTime,
      'max_uses': maxUses,
    });

    if (!mounted) return;
    AppFeedback.fromResult(context, result);
    if (result.ok) await _fetchMembers();
  }

  Future<void> _showEditRoleDialog(Map<String, dynamic> member) async {
    var selectedRole = asText(member['role'], fallback: 'Guest');
    var isTempAccess =
        member['start_time'] != null || member['end_time'] != null;
    var hasUsesLimit = member['max_uses'] != null;
    var startDate = asDateTime(member['start_time']);
    var endDate = asDateTime(member['end_time']);

    final maxUsesController =
        TextEditingController(text: asInt(member['max_uses'])?.toString() ?? '');

    try {
      final saved = await showDialog<bool>(
        context: context,
        builder: (dialogContext) => StatefulBuilder(
          builder: (context, setDialogState) {
            Future<DateTime?> pickDateTime(DateTime? initial) async {
              final date = await showDatePicker(
                context: context,
                initialDate: initial ?? DateTime.now(),
                firstDate: DateTime.now().subtract(const Duration(days: 365)),
                lastDate: DateTime.now().add(const Duration(days: 365 * 2)),
              );
              if (date == null || !context.mounted) return null;

              final time = await showTimePicker(
                context: context,
                initialTime: TimeOfDay.fromDateTime(initial ?? DateTime.now()),
              );
              if (time == null) return null;

              return DateTime(
                  date.year, date.month, date.day, time.hour, time.minute);
            }

            return AlertDialog(
              title: Row(
                children: [
                  const Icon(Icons.security_rounded, color: AppColors.purple),
                  const SizedBox(width: AppSpacing.sm),
                  Expanded(
                    child: Text(
                      '編輯 ${asText(member['username'])} 的權限',
                      style: const TextStyle(fontSize: 17),
                    ),
                  ),
                ],
              ),
              content: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('權限角色',
                        style: TextStyle(
                            color: AppColors.textSecondary,
                            fontSize: 13,
                            fontWeight: FontWeight.bold)),
                    const SizedBox(height: AppSpacing.sm),
                    Container(
                      padding:
                          const EdgeInsets.symmetric(horizontal: AppSpacing.md - 4),
                      decoration: BoxDecoration(
                        color: Colors.white.withValues(alpha: 0.05),
                        borderRadius: BorderRadius.circular(AppRadius.md),
                        border: Border.all(color: AppColors.border),
                      ),
                      child: DropdownButtonHideUnderline(
                        child: DropdownButton<String>(
                          value: selectedRole,
                          dropdownColor: AppColors.surface,
                          isExpanded: true,
                          style: const TextStyle(color: AppColors.textPrimary),
                          items: const [
                            'Admin',
                            'Member',
                            'Guest',
                            'Technician',
                            'SP',
                            'Revoked',
                          ]
                              .map((role) => DropdownMenuItem(
                                    value: role,
                                    child: Text('${roleLabel(role)}  ($role)'),
                                  ))
                              .toList(),
                          onChanged: (value) {
                            if (value != null) {
                              setDialogState(() => selectedRole = value);
                            }
                          },
                        ),
                      ),
                    ),
                    const SizedBox(height: AppSpacing.md),
                    CheckboxListTile(
                      contentPadding: EdgeInsets.zero,
                      value: isTempAccess,
                      activeColor: AppColors.purple,
                      title: const Text('啟用臨時權限時間限制',
                          style: TextStyle(
                              color: AppColors.textPrimary, fontSize: 14)),
                      onChanged: (value) => setDialogState(() {
                        isTempAccess = value ?? false;
                        if (isTempAccess) {
                          startDate ??= DateTime.now();
                          endDate ??=
                              DateTime.now().add(const Duration(days: 1));
                        }
                      }),
                    ),
                    if (isTempAccess) ...[
                      _dateRow('開始時間', startDate, () async {
                        final picked = await pickDateTime(startDate);
                        if (picked != null) {
                          setDialogState(() => startDate = picked);
                        }
                      }),
                      const SizedBox(height: AppSpacing.sm),
                      _dateRow('結束時間', endDate, () async {
                        final picked = await pickDateTime(endDate);
                        if (picked != null) {
                          setDialogState(() => endDate = picked);
                        }
                      }),
                    ],
                    CheckboxListTile(
                      contentPadding: EdgeInsets.zero,
                      value: hasUsesLimit,
                      activeColor: AppColors.purple,
                      title: const Text('啟用操作次數限制',
                          style: TextStyle(
                              color: AppColors.textPrimary, fontSize: 14)),
                      onChanged: (value) =>
                          setDialogState(() => hasUsesLimit = value ?? false),
                    ),
                    if (hasUsesLimit)
                      TextField(
                        controller: maxUsesController,
                        keyboardType: TextInputType.number,
                        style: const TextStyle(color: AppColors.textPrimary),
                        decoration: InputDecoration(
                          hintText: '最大允許操作次數',
                          hintStyle: TextStyle(color: AppColors.textDisabled),
                          enabledBorder: UnderlineInputBorder(
                            borderSide: BorderSide(color: AppColors.border),
                          ),
                          focusedBorder: const UnderlineInputBorder(
                            borderSide: BorderSide(color: AppColors.purple),
                          ),
                        ),
                      ),
                  ],
                ),
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.of(dialogContext).pop(false),
                  child: Text('取消',
                      style: TextStyle(color: AppColors.textTertiary)),
                ),
                ElevatedButton(
                  onPressed: () => Navigator.of(dialogContext).pop(true),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppColors.indigo,
                    foregroundColor: Colors.white,
                  ),
                  child: const Text('更新權限'),
                ),
              ],
            );
          },
        ),
      );

      if (saved != true || !mounted) return;

      await _updateMemberRole(
        targetUid: asText(member['user_id'], fallback: ''),
        targetRole: selectedRole,
        startTime:
            isTempAccess && startDate != null ? formatForApi(startDate!) : null,
        endTime:
            isTempAccess && endDate != null ? formatForApi(endDate!) : null,
        maxUses: hasUsesLimit ? int.tryParse(maxUsesController.text) : null,
      );
    } finally {
      maxUsesController.dispose();
    }
  }

  Widget _dateRow(String label, DateTime? value, VoidCallback onTap) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(AppRadius.sm),
      child: Container(
        padding: const EdgeInsets.all(AppSpacing.md - 4),
        decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: 0.04),
          borderRadius: BorderRadius.circular(AppRadius.sm),
          border: Border.all(color: AppColors.border),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text('$label: ',
                style:
                    TextStyle(color: AppColors.textTertiary, fontSize: 13)),
            Text(
              value == null ? '未設定' : formatDisplay(value),
              style: const TextStyle(
                  color: AppColors.textPrimary,
                  fontSize: 13,
                  fontWeight: FontWeight.bold),
            ),
          ],
        ),
      ),
    );
  }

  // ------------------------------------------------------------------
  // UC1.5 場域切換
  // ------------------------------------------------------------------

  /// 從標題列直接切換到另一個場域。
  ///
  /// 切換時會用 `pushReplacement`，避免一路切下去堆出無限深的返回堆疊。
  /// 角色是逐場域的（同一個帳號在 A 屋是 Admin、在 B 屋可能只是 Guest），
  /// 因此新畫面會帶入該場域自己的 `user_role`，而不是沿用目前的角色。
  Future<void> _showFamilySwitcher() async {
    final result = await ApiClient.post(ApiEndpoints.getUserFamilies, {
      'user_id': widget.currentUserId,
    });

    if (!mounted) return;

    if (!result.ok) {
      AppFeedback.fromResult(context, result);
      return;
    }

    final families = result.dataList;
    if (families.length <= 1) {
      AppFeedback.info(context, '您目前只有這一個場域');
      return;
    }

    final selected = await showModalBottomSheet<Map<String, dynamic>>(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(AppRadius.xl)),
      ),
      builder: (sheetContext) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.all(AppSpacing.md),
              child: Text(
                '切換場域',
                style: TextStyle(
                  color: AppColors.textSecondary,
                  fontSize: 14,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ),
            Flexible(
              child: ListView.builder(
                shrinkWrap: true,
                itemCount: families.length,
                itemBuilder: (context, index) {
                  final fam = families[index] as Map<String, dynamic>;
                  final id = asInt(fam['family_id']);
                  final isCurrent = id == widget.familyId;

                  return ListTile(
                    leading: Icon(
                      Icons.home_outlined,
                      color: isCurrent
                          ? AppColors.purpleLight
                          : AppColors.textTertiary,
                    ),
                    title: Text(
                      asText(fam['family_name'], fallback: '未命名場域'),
                      style: TextStyle(
                        color: AppColors.textPrimary,
                        fontWeight:
                            isCurrent ? FontWeight.bold : FontWeight.normal,
                      ),
                    ),
                    subtitle: Text(
                      '${roleLabel(fam['user_role'])} · '
                      '${asInt(fam['device_count']) ?? 0} 台裝置',
                      style: TextStyle(
                          color: AppColors.textDisabled, fontSize: 12),
                    ),
                    trailing: isCurrent
                        ? const Icon(Icons.check, color: AppColors.success)
                        : null,
                    onTap: isCurrent
                        ? null
                        : () => Navigator.of(sheetContext).pop(fam),
                  );
                },
              ),
            ),
            const SizedBox(height: AppSpacing.sm),
          ],
        ),
      ),
    );

    if (selected == null || !mounted) return;

    final newId = asInt(selected['family_id']);
    if (newId == null) return;

    Navigator.pushReplacement(
      context,
      MaterialPageRoute(
        builder: (_) => FamilyDetailScreen(
          familyId: newId,
          familyName: asText(selected['family_name'], fallback: '未命名場域'),
          myRole: asText(selected['user_role'], fallback: 'Guest'),
          currentUserId: widget.currentUserId,
        ),
      ),
    );
  }

  // ------------------------------------------------------------------
  // Build
  // ------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        // UC1.5 跨房屋情境切換：直接在標題列換場域，不用退回清單再點進來。
        title: InkWell(
          onTap: _showFamilySwitcher,
          borderRadius: BorderRadius.circular(AppRadius.sm),
          child: Padding(
            padding: const EdgeInsets.symmetric(
                horizontal: AppSpacing.xs, vertical: 2),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Flexible(
                  child: Text(
                    widget.familyName,
                    style: const TextStyle(fontWeight: FontWeight.bold),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                const SizedBox(width: AppSpacing.xs),
                Icon(Icons.expand_more,
                    size: 20, color: AppColors.textTertiary),
              ],
            ),
          ),
        ),
        actions: [
          IconButton(
            tooltip: '場域儀表板',
            icon: const Icon(Icons.dashboard_outlined),
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(
                builder: (_) => DashboardScreen(
                  familyId: widget.familyId,
                  familyName: widget.familyName,
                  currentUserId: widget.currentUserId,
                  myRole: widget.myRole,
                ),
              ),
            ).then((_) => _fetchDevices()),
          ),
          if (_isAdmin)
            IconButton(
              tooltip: '訪客授權 QR',
              icon: const Icon(Icons.qr_code_2),
              onPressed: () => Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (_) => GuestQrScreen(
                    familyId: widget.familyId,
                    familyName: widget.familyName,
                    adminUid: widget.currentUserId,
                  ),
                ),
              ),
            ),
        ],
        bottom: TabBar(
          controller: _tabController,
          indicatorColor: AppColors.purple,
          labelColor: AppColors.textPrimary,
          unselectedLabelColor: AppColors.textDisabled,
          tabs: [
            const Tab(text: '成員清單', icon: Icon(Icons.people_outline)),
            const Tab(text: '設備狀態', icon: Icon(Icons.router_outlined)),
            if (_isAdmin)
              const Tab(
                  text: '發送邀請', icon: Icon(Icons.person_add_alt_1_outlined)),
          ],
        ),
      ),
      floatingActionButton: _isAdmin && _tabController.index == 1
          ? FloatingActionButton.extended(
              onPressed: _pairNewDevice,
              backgroundColor: AppColors.purple,
              foregroundColor: Colors.white,
              icon: const Icon(Icons.add_link),
              label: const Text('配對裝置'),
            )
          : null,
      body: Stack(
        children: [
          const Positioned(
            top: -100,
            left: -80,
            child: GlowOrb(
                color: AppColors.purple, diameter: 300, opacity: 0.08),
          ),
          TabBarView(
            controller: _tabController,
            children: [
              _buildMembersTab(),
              _buildDevicesTab(),
              if (_isAdmin) _buildInviteTab(),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildMembersTab() {
    if (_isLoadingMembers && _members.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }

    if (_members.isEmpty) {
      return RefreshIndicator(
        onRefresh: _fetchMembers,
        color: AppColors.purple,
        backgroundColor: AppColors.surface,
        child: EmptyState(
          icon: Icons.people_outline,
          title: _membersError == null ? '這個場域還沒有成員' : '無法載入成員清單',
          subtitle: _membersError,
          isError: _membersError != null,
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: _fetchMembers,
      color: AppColors.purple,
      backgroundColor: AppColors.surface,
      child: ListView.builder(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.all(AppSpacing.md),
        itemCount: _members.length,
        itemBuilder: (context, index) {
          final member = _members[index] as Map<String, dynamic>;
          return _buildMemberCard(member);
        },
      ),
    );
  }

  Widget _buildMemberCard(Map<String, dynamic> member) {
    final role = asText(member['role'], fallback: 'Guest');
    final isSelf = asBool(member['is_self']);
    final isOwner = asBool(member['is_family_admin']);
    final isRevoked = role.toLowerCase() == 'revoked';

    return Container(
      margin: const EdgeInsets.only(bottom: AppSpacing.md - 4),
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.surface.withValues(alpha: 0.6),
        borderRadius: BorderRadius.circular(AppRadius.lg),
        border: Border.all(color: AppColors.border),
      ),
      child: Row(
        children: [
          Container(
            width: 42,
            height: 42,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              gradient: isRevoked ? null : AppColors.accentGradient,
              color: isRevoked ? Colors.white.withValues(alpha: 0.06) : null,
            ),
            child: Center(
              child: Text(
                initialOf(asText(member['username'], fallback: '')),
                style: TextStyle(
                  color: isRevoked ? AppColors.textDisabled : Colors.white,
                  fontSize: 18,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ),
          ),
          const SizedBox(width: AppSpacing.md - 4),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Flexible(
                      child: Text(
                        asText(member['username']),
                        style: TextStyle(
                          color: isRevoked
                              ? AppColors.textTertiary
                              : AppColors.textPrimary,
                          fontSize: 15,
                          fontWeight: FontWeight.bold,
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    if (isSelf) _tag('您', AppColors.indigo),
                    if (isOwner) _tag('場域擁有者', AppColors.purple),
                  ],
                ),
                const SizedBox(height: 2),
                Text(
                  asText(member['user_id']),
                  style: TextStyle(
                    color: AppColors.textDisabled,
                    fontSize: 11,
                    fontFamily: 'monospace',
                  ),
                ),
                const SizedBox(height: AppSpacing.xs),
                Row(
                  children: [
                    Icon(Icons.shield_outlined,
                        size: 13,
                        color: isRevoked
                            ? AppColors.danger
                            : AppColors.indigoLight),
                    const SizedBox(width: AppSpacing.xs),
                    Text(
                      roleLabel(role),
                      style: TextStyle(
                        color: isRevoked
                            ? AppColors.danger
                            : AppColors.indigoLight,
                        fontSize: 12,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    if (member['end_time'] != null) ...[
                      const SizedBox(width: AppSpacing.sm),
                      Icon(Icons.schedule,
                          size: 12, color: AppColors.textDisabled),
                      const SizedBox(width: 2),
                      Flexible(
                        child: Text(
                          '至 ${formatDisplay(member['end_time'])}',
                          style: TextStyle(
                              color: AppColors.textDisabled, fontSize: 11),
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ],
                  ],
                ),
              ],
            ),
          ),
          // 不能編輯自己的權限（後端 update_member_role.py 也有自我撤權保護）。
          if (_isAdmin && !isSelf)
            IconButton(
              icon: Icon(Icons.edit_outlined,
                  color: AppColors.textTertiary, size: 20),
              tooltip: '編輯權限',
              onPressed: () => _showEditRoleDialog(member),
            ),
        ],
      ),
    );
  }

  Widget _tag(String label, Color color) => Container(
        margin: const EdgeInsets.only(left: AppSpacing.sm),
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.2),
          borderRadius: BorderRadius.circular(4),
        ),
        child: Text(
          label,
          style: TextStyle(
              color: color, fontSize: 10, fontWeight: FontWeight.bold),
        ),
      );

  Widget _buildDevicesTab() {
    if (_isLoadingDevices && _devices.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }

    if (_devices.isEmpty) {
      return RefreshIndicator(
        onRefresh: _fetchDevices,
        color: AppColors.purple,
        backgroundColor: AppColors.surface,
        child: EmptyState(
          icon: Icons.router_outlined,
          title: _devicesError == null ? '這個場域還沒有裝置' : '無法載入裝置清單',
          subtitle: _devicesError ??
              (_isAdmin ? '點右下角「配對裝置」新增第一台設備。' : '請聯絡場域管理員配對裝置。'),
          isError: _devicesError != null,
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: _fetchDevices,
      color: AppColors.purple,
      backgroundColor: AppColors.surface,
      child: ListView.builder(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(
            AppSpacing.md, AppSpacing.md, AppSpacing.md, 96),
        itemCount: _devices.length,
        itemBuilder: (context, index) {
          final device = _devices[index] as Map<String, dynamic>;
          return DeviceCard(
            device: device,
            isAdmin: _isAdmin,
            canControl: _canControl,
            myRole: widget.myRole,
            onAction: (action) => _handleDeviceAction(action, device),
          );
        },
      ),
    );
  }

  Widget _buildInviteTab() {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(AppSpacing.lg),
      child: Form(
        key: _inviteFormKey,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              '邀請已註冊的帳號加入「${widget.familyName}」。'
              '對方會在「邀請通知」看到這則邀請，接受後才會正式加入。',
              style: TextStyle(
                  color: AppColors.textTertiary, fontSize: 13, height: 1.5),
            ),
            const SizedBox(height: AppSpacing.lg),
            AppTextField(
              controller: _inviteeIdController,
              labelText: '受邀者帳號 ID',
              hintText: '請輸入對方註冊時的帳號',
              prefixIcon: Icons.person_search_outlined,
              enabled: !_isSendingInvite,
              validator: (value) => (value == null || value.trim().isEmpty)
                  ? '請輸入受邀者帳號'
                  : null,
            ),
            const SizedBox(height: AppSpacing.md),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
              decoration: BoxDecoration(
                color: AppColors.surface.withValues(alpha: 0.6),
                borderRadius: BorderRadius.circular(AppRadius.lg),
                border: Border.all(color: AppColors.border),
              ),
              child: DropdownButtonHideUnderline(
                child: DropdownButton<String>(
                  value: _inviteRole,
                  isExpanded: true,
                  dropdownColor: AppColors.surface,
                  style: const TextStyle(color: AppColors.textPrimary),
                  items: const ['Member', 'Guest', 'Technician', 'SP']
                      .map((role) => DropdownMenuItem(
                            value: role,
                            child: Text('${roleLabel(role)}  ($role)'),
                          ))
                      .toList(),
                  onChanged: _isSendingInvite
                      ? null
                      : (value) {
                          if (value != null) {
                            setState(() => _inviteRole = value);
                          }
                        },
                ),
              ),
            ),
            const SizedBox(height: AppSpacing.lg),
            AppPrimaryButton(
              label: '發送邀請',
              icon: Icons.send_rounded,
              isLoading: _isSendingInvite,
              onPressed: _sendInvitation,
            ),
          ],
        ),
      ),
    );
  }
}
