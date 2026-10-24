-- 2026-10-24：guest_tokens 補 valid_from（訪客 QR「未來才生效」用）
-- 只給「已經跑過的舊 DB」用；新建的 DB 由 schema.sql 直接建好，不用跑。
-- 可重複執行：欄位已存在就什麼都不做（MySQL 8.0 沒有 ADD COLUMN IF NOT EXISTS）。
--
-- 執行：
--   docker exec -i iot_mysql mysql -uroot -pdevroot123 devicemanagement < API/migrations/2026-10-24_guest_tokens_valid_from.sql

SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'guest_tokens' AND COLUMN_NAME = 'valid_from'
);
SET @ddl := IF(@col_exists = 0,
  'ALTER TABLE guest_tokens ADD COLUMN valid_from DATETIME NULL AFTER allowed_actions',
  'SELECT ''guest_tokens.valid_from 已存在，略過'' AS msg');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SHOW COLUMNS FROM guest_tokens LIKE 'valid_from';
