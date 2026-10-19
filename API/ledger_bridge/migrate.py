"""Run once (and safely rerun): python -m ledger_bridge.migrate.
MySQL DDL auto-commits. Back up first; never run concurrent migrations.
"""
from .db import connect

COLUMNS = {
    'worker_id': 'VARCHAR(128) NULL',
    'claim_token': 'CHAR(64) NULL',
    'lease_until': 'DATETIME(6) NULL',
    'next_retry_at': 'DATETIME(6) NULL',
    'confirmation_method': 'VARCHAR(128) NULL',
}

def main():
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM ledger_events")
            existing = {r['Field'] for r in cur.fetchall()}
            required = {'event_id', 'payload', 'payload_hash', 'status', 'ledger_reference',
                        'retry_count', 'last_error', 'created_at', 'submitted_at', 'confirmed_at'}
            if not required <= existing:
                raise RuntimeError('Base ledger_events schema missing required columns: ' + str(required-existing))
            for name, definition in COLUMNS.items():
                if name not in existing:
                    cur.execute(f'ALTER TABLE ledger_events ADD COLUMN `{name}` {definition}')
            cur.execute('SHOW INDEX FROM ledger_events')
            indexes = {r['Key_name'] for r in cur.fetchall()}
            if 'idx_ledger_bridge_queue' not in indexes:
                cur.execute('CREATE INDEX idx_ledger_bridge_queue ON ledger_events (status, next_retry_at, lease_until)')
            cur.execute('''CREATE TABLE IF NOT EXISTS ledger_bridge_receipts (
                event_id VARCHAR(191) NOT NULL,
                claim_token CHAR(64) NOT NULL,
                action VARCHAR(16) NOT NULL,
                request_hash CHAR(64) NOT NULL,
                response JSON NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (event_id, claim_token, action)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
        conn.commit()
        print('Ledger Bridge migration complete (existing event payloads unchanged).')
    finally:
        conn.close()

if __name__ == '__main__':
    main()
