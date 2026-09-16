import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// 統一的空狀態 / 錯誤狀態呈現。
///
/// 用 [ListView] 包起來是為了讓外層的 [RefreshIndicator] 在沒有內容時
/// 依然能下拉重整 —— 原本各畫面用 `Center` 包住，內容不可捲動，
/// 使用者在空清單時反而無法手動重試。
class EmptyState extends StatelessWidget {
  const EmptyState({
    super.key,
    required this.icon,
    required this.title,
    this.subtitle,
    this.isError = false,
    this.action,
  });

  final IconData icon;
  final String title;
  final String? subtitle;

  /// 錯誤狀態用警告色，跟「單純沒有資料」區分開來。
  final bool isError;

  final Widget? action;

  @override
  Widget build(BuildContext context) {
    final accent = isError ? AppColors.warning : AppColors.textDisabled;

    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.lg),
      children: [
        SizedBox(height: MediaQuery.of(context).size.height * 0.15),
        Icon(icon, size: 72, color: accent.withValues(alpha: 0.35)),
        const SizedBox(height: AppSpacing.md),
        Text(
          title,
          textAlign: TextAlign.center,
          style: TextStyle(
            color: isError ? AppColors.warning : AppColors.textTertiary,
            fontSize: 16,
            fontWeight: FontWeight.bold,
          ),
        ),
        if (subtitle != null) ...[
          const SizedBox(height: AppSpacing.sm),
          Text(
            subtitle!,
            textAlign: TextAlign.center,
            style: TextStyle(
              color: AppColors.textDisabled,
              fontSize: 13,
              height: 1.5,
            ),
          ),
        ],
        if (action != null) ...[
          const SizedBox(height: AppSpacing.lg),
          Center(child: action),
        ],
        const SizedBox(height: AppSpacing.lg),
        Center(
          child: Text(
            '下拉可重新整理',
            style: TextStyle(color: AppColors.textDisabled, fontSize: 11),
          ),
        ),
      ],
    );
  }
}
