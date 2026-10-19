import os
from contextlib import contextmanager
import pymysql


def connect():
    return pymysql.connect(
        host=os.environ.get('DB_HOST', 'mysql'),
        port=int(os.environ.get('DB_PORT', '3306')),
        user=os.environ.get('DB_USER', 'apiuser'),
        password=os.environ.get('DB_PASSWORD', os.environ.get('DB_PASS', '')),
        database=os.environ.get('DB_NAME', 'devicemanagement'),
        charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor,
        autocommit=False, connect_timeout=5, read_timeout=15, write_timeout=15,
        init_command="SET time_zone = '+00:00'")

@contextmanager
def transaction():
    conn = connect()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
