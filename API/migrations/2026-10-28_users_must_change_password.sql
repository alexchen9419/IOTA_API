-- 2026-10-28：管理員協助重設密碼（/admin_reset_password）用的 users.must_change_password
-- 只給「已經跑過的舊 DB」用；新建的 DB 由 schema.sql 直接建好，不用跑。
-- 可重複執行。沒跑也能運作：login 不回這個旗標、App 不會強制改密碼，
-- 但 /admin_reset_password 會回 500 要求先補欄位（避免臨時密碼變成永久密碼）。
--
-- 執行：
--   docker exec -i iot_mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" devicemanagement' < API/migrations/2026-10-28_users_must_change_password.sql

SET @has_col := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' AND COLUMN_NAME = 'must_change_password'
);
SET @ddl := IF(@has_col = 0,
  'ALTER TABLE users ADD COLUMN must_change_password TINYINT(1) NOT NULL DEFAULT 0 AFTER status',
  'SELECT ''must_change_password already exists''');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SHOW COLUMNS FROM users LIKE 'must_change_password';
