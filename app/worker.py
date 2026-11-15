"""Фоновый обработчик очереди писем (лаба 10). Запуск: python -m app.worker"""
import logging
import signal
import time

from app.config import get_settings
from app.infra import EmailQueue, make_redis
from app.logging_setup import setup_logging

log = logging.getLogger("app.worker")


def main(poll_interval: float = 1.0) -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    queue = EmailQueue(make_redis(settings.redis_url))
    running = {"on": True}

    def stop(*_):
        running["on"] = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("worker started")
    while running["on"]:
        try:
            if queue.process() == 0:
                time.sleep(poll_interval)
        except Exception:  # noqa: BLE001
            log.exception("worker iteration failed")
            time.sleep(poll_interval * 5)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
