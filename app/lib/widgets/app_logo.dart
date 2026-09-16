import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// App 的六角形發光標誌。
///
/// 原本這個 painter 定義在 `register_screen.dart` 裡，`login_screen` 為了用它
/// 而 import 整個註冊畫面 —— 兩個畫面因此互相耦合。抽到這裡之後兩邊都只依賴
/// 這個元件。
///
/// 原始實作有幾個問題一併修掉：
///   * 用手刻的區間查表近似 cos/sin（註解寫「dart:math 不是必要的」，
///     但 dart:math 一直都在標準函式庫裡）。近似值只在 60 度整數倍附近正確，
///     且邊界條件寫死，改角度數量就會畫歪。
///   * `double.parse((1.0 * (1.0 + 0.15)).toString())` 是一個把數字轉字串
///     再parse回來的空操作，實際等同常數 1.15。
class AppLogo extends StatelessWidget {
  const AppLogo({super.key, this.size = 80});

  final double size;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: size,
      height: size,
      child: CustomPaint(painter: _AppLogoPainter()),
    );
  }
}

class _AppLogoPainter extends CustomPainter {
  /// 六角形外框半徑（相對於 80x80 的畫布）。
  static const double _radius = 32.0;

  /// 原始碼裡那串常數運算的結果，保留視覺比例不變。
  static const double _scale = 1.15;

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);

    // --- 外框：六角形 ---
    final path = Path();
    for (int i = 0; i < 6; i++) {
      final angle = i * 60 * math.pi / 180;
      // 交錯的長短半徑 + 頂點微調，維持原本略帶不規則的手繪感。
      final r = _radius *
          (i.isEven ? 1.0 : 0.8) *
          _scale *
          (i == 0 || i == 3 ? 1.0 : 0.95);
      final point = Offset(
        center.dx + r * math.cos(angle),
        center.dy + r * math.sin(angle),
      );
      if (i == 0) {
        path.moveTo(point.dx, point.dy);
      } else {
        path.lineTo(point.dx, point.dy);
      }
    }
    path.close();

    final framePaint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 4.0
      ..strokeJoin = StrokeJoin.round
      ..shader = const LinearGradient(
        colors: [AppColors.indigo, AppColors.pink],
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
      ).createShader(Rect.fromCircle(center: center, radius: _radius));

    canvas.drawPath(path, framePaint);

    // --- 內部：使用者剪影 ---
    final userPaint = Paint()
      ..style = PaintingStyle.fill
      ..shader = const LinearGradient(
        colors: [AppColors.purple, Color(0xFF3B82F6)],
      ).createShader(Rect.fromCircle(center: center, radius: 15));

    canvas.drawCircle(Offset(center.dx, center.dy - 6), 7, userPaint);

    final shoulders = Path()
      ..moveTo(center.dx - 14, center.dy + 12)
      ..quadraticBezierTo(
        center.dx,
        center.dy + 3,
        center.dx + 14,
        center.dy + 12,
      )
      ..close();
    canvas.drawPath(shoulders, userPaint);
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}
