import 'package:flutter/material.dart';

import '../theme/app_theme.dart';
import 'api_client.dart';

/// 統一的訊息提示。
///
/// 原本 `login_screen` 有 `_showErrorSnackBar`、其他三個畫面各自有一份
/// `_showToast`，樣式與行為都略有出入。這裡集中一份，並且每個入口都先檢查
/// `context.mounted` —— 這些方法幾乎都在 `await` 之後被呼叫，畫面可能已經
/// 被 pop 掉了。
class AppFeedback {
  AppFeedback._();

  static void success(BuildContext context, String message) =>
      _show(context, message, AppColors.success, Icons.check_circle_outline);

  static void error(BuildContext context, String message) =>
      _show(context, message, AppColors.danger, Icons.error_outline);

  static void warning(BuildContext context, String message) =>
      _show(context, message, AppColors.warning, Icons.warning_amber_rounded);

  static void info(BuildContext context, String message) =>
      _show(context, message, AppColors.indigo, Icons.info_outline);

  /// 依 [ApiResult] 自動選擇樣式：連線問題用警告色並提示可以去設定頁，
  /// 業務錯誤用紅色直接顯示後端訊息。
  static void fromResult(BuildContext context, ApiResult result) {
    if (result.ok) {
      success(context, result.message);
      return;
    }
    if (result.isNetworkError) {
      warning(context, result.message);
      return;
    }
    error(context, result.message);
  }

  static void _show(
    BuildContext context,
    String message,
    Color color,
    IconData icon,
  ) {
    if (!context.mounted) return;

    final messenger = ScaffoldMessenger.maybeOf(context);
    if (messenger == null) return;

    messenger
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          content: Row(
            children: [
              Icon(icon, color: Colors.white, size: 22),
              const SizedBox(width: AppSpacing.md - 4),
              Expanded(
                child: Text(
                  message,
                  style: const TextStyle(
                    color: Colors.white,
                    fontWeight: FontWeight.bold,
                    fontSize: 14,
                  ),
                ),
              ),
            ],
          ),
          backgroundColor: color,
          behavior: SnackBarBehavior.floating,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AppRadius.md),
          ),
          margin: const EdgeInsets.all(AppSpacing.md),
          duration: const Duration(seconds: 4),
        ),
      );
  }
}
