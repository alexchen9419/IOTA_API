-- 2026-10-28：audit_logs 雜湊鏈寫入鎖（common/audit_log_service.py 使用）
-- 只給「已經跑過的舊 DB」用；新建的 DB 由 schema.sql 直接建好，不用跑。
-- 可重複執行。沒跑也能運作（模組會退回只用 audit_logs 的 SELECT ... FOR UPDATE），
-- 但高並行時可能出現 deadlock 而讓其中一個請求失敗；建議補跑。
--
-- 執行：
--   docker exec -i iot_mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" devicemanagement' < API/migrations/2026-10-28_audit_chain_lock.sql

CREATE TABLE IF NOT EXISTS audit_chain_lock (
  id TINYINT UNSIGNED NOT NULL PRIMARY KEY
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
INSERT IGNORE INTO audit_chain_lock (id) VALUES (1);

SELECT * FROM audit_chain_lock;
