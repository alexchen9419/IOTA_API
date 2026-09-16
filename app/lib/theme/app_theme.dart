import 'package:flutter/material.dart';

/// App 的配色與共用樣式。
///
/// 原本這組深色靛紫粉（`0xFF0F172A` / `0xFF1E293B` / `0xFF6366F1` /
/// `0xFF8B5CF6` / `0xFFEC4899`）在四個畫面裡硬編碼重複了數十次，改一個顏色
/// 要翻四個檔案；而 `main.dart` 的 seed color 還停在範本的 deepPurple，跟實際
/// 畫面對不上。這裡集中定義，畫面端一律引用常數。
class AppColors {
  AppColors._();

  // 底色（Slate 900 / 800）
  static const Color background = Color(0xFF0F172A);
  static const Color surface = Color(0xFF1E293B);

  // 主色漸層：靛 → 紫 → 粉
  static const Color indigo = Color(0xFF6366F1);
  static const Color purple = Color(0xFF8B5CF6);
  static const Color pink = Color(0xFFEC4899);

  // 主色的淺色變體，用在文字/圖示上確保對比度
  static const Color indigoLight = Color(0xFF818CF8);
  static const Color purpleLight = Color(0xFFC084FC);
  static const Color pinkLight = Color(0xFFF472B6);

  // 語意色
  static const Color success = Color(0xFF10B981);
  static const Color danger = Color(0xFFEF4444);
  static const Color dangerLight = Color(0xFFF87171);
  static const Color warning = Color(0xFFF59E0B);
  static const Color info = Color(0xFF38BDF8);

  // 裝置狀態（對應後端 dashboard 的 connection_health）
  static const Color healthGood = Color(0xFF10B981);
  static const Color healthWeak = Color(0xFFF59E0B);
  static const Color healthOffline = Color(0xFF64748B);
  static const Color healthFault = Color(0xFFEF4444);

  // 文字階層（白色不同透明度）
  static const Color textPrimary = Colors.white;
  static Color textSecondary = Colors.white.withValues(alpha: 0.7);
  static Color textTertiary = Colors.white.withValues(alpha: 0.5);
  static Color textDisabled = Colors.white.withValues(alpha: 0.3);

  // 邊框與分隔線
  static Color border = Colors.white.withValues(alpha: 0.08);
  static Color borderStrong = Colors.white.withValues(alpha: 0.16);

  /// 主要按鈕與標題用的漸層。
  static const LinearGradient primaryGradient = LinearGradient(
    colors: [indigo, purple, pink],
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
  );

  /// 頭像、圖示方塊用的較短漸層。
  static const LinearGradient accentGradient = LinearGradient(
    colors: [indigo, purple],
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
  );
}

/// 共用的圓角、間距常數，避免每個畫面各自寫 magic number。
class AppRadius {
  AppRadius._();
  static const double sm = 10;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 20;
}

class AppSpacing {
  AppSpacing._();
  static const double xs = 4;
  static const double sm = 8;
  static const double md = 16;
  static const double lg = 24;
  static const double xl = 32;
}

class AppTheme {
  AppTheme._();

  static ThemeData get dark {
    final base = ThemeData.dark(useMaterial3: true);

    return base.copyWith(
      scaffoldBackgroundColor: AppColors.background,
      colorScheme: ColorScheme.fromSeed(
        // 這裡跟畫面實際用的主色一致（原本是範本的 deepPurple）。
        seedColor: AppColors.purple,
        brightness: Brightness.dark,
      ).copyWith(
        surface: AppColors.surface,
        primary: AppColors.indigo,
        secondary: AppColors.purple,
        error: AppColors.danger,
      ),
      appBarTheme: AppBarTheme(
        backgroundColor: AppColors.surface.withValues(alpha: 0.6),
        foregroundColor: AppColors.textPrimary,
        elevation: 0,
        iconTheme: const IconThemeData(color: AppColors.textPrimary),
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: AppColors.surface,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.xl),
        ),
        titleTextStyle: const TextStyle(
          color: AppColors.textPrimary,
          fontSize: 18,
          fontWeight: FontWeight.bold,
        ),
        contentTextStyle: TextStyle(color: AppColors.textSecondary),
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.md),
        ),
        contentTextStyle: const TextStyle(
          color: Colors.white,
          fontWeight: FontWeight.bold,
        ),
      ),
      progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: AppColors.purple,
      ),
      dividerTheme: DividerThemeData(color: AppColors.border),
    );
  }
}
