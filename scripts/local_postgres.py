"""Локальный PostgreSQL без Docker (через pgserver). Печатает DSN для DATABASE_URL / TEST_DATABASE_URL.

    python scripts/local_postgres.py            # запустить и держать, пока не нажмёте Ctrl+C
"""
import os
import sys
import time
from pathlib import Path

DATA_DIR = Path(os.getenv("PGSERVER_DIR", Path.home() / ".library-pgdata"))


def get_dsn(cleanup: str = "stop") -> str:
    import pgserver

    srv = pgserver.get_server(str(DATA_DIR), cleanup_mode=cleanup)
    return srv.get_uri()


if __name__ == "__main__":
    dsn = get_dsn(cleanup=None)
    print(dsn)
    sys.stdout.flush()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
