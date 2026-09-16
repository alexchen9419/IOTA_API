import 'package:flutter/material.dart';

import 'screens/family_list_screen.dart';
import 'screens/login_screen.dart';
import 'theme/app_theme.dart';
import 'utils/api_config.dart';
import 'utils/session.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // 兩者都要在第一個畫面出現之前完成：
  //   ApiConfig — 讀回使用者設定的伺服器位址，否則會先用預設值打一輪失敗。
  //   Session   — 讀回上次登入的身分，決定要進登入頁還是主畫面。
  await ApiConfig.init();
  await Session.restore();

  runApp(const IotaApp());
}

class IotaApp extends StatelessWidget {
  const IotaApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'IOTA 智慧鎖',
      debugShowCheckedModeBanner: false,
      // 整個 App 是深色設計；darkTheme 也指到同一份，避免使用者系統設為淺色時
      // 只有部分元件跟著變、造成配色破碎。
      theme: AppTheme.dark,
      darkTheme: AppTheme.dark,
      themeMode: ThemeMode.dark,
      home: Session.isLoggedIn
          ? FamilyListScreen(userData: Session.current!)
          : const LoginScreen(),
    );
  }
}
