"""Общий Redis для нескольких локальных экземпляров без Docker (fakeredis по TCP, протокол Redis).

    python scripts/local_redis.py 6379      # затем REDIS_URL=redis://127.0.0.1:6379/0
В Docker вместо этого работает настоящий redis:7-alpine.
"""
import sys

from fakeredis import TcpFakeServer

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 6379
    server = TcpFakeServer(("127.0.0.1", port), server_type="redis")
    print(f"fake redis on 127.0.0.1:{port}", flush=True)
    server.serve_forever()
