import json
import logging
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import redis
from psycopg_pool import PoolTimeout
from prometheus_client import Counter, Gauge, Histogram, start_http_server

from uptime import checker, db, settings

log = logging.getLogger("worker")

TARGET_UP = Gauge("uptime_target_up", "1 if the last check succeeded", ["target"])
RESPONSE_SECONDS = Gauge("uptime_response_seconds", "Response time of the last check", ["target"])
CHECK_DURATION = Histogram(
    "uptime_check_duration_seconds", "Response time of checks", ["target"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)
CHECKS = Counter("uptime_checks_total", "Checks run", ["target", "result"])
LAST_ROUND = Gauge("uptime_last_round_timestamp_seconds", "When the last scheduled round finished")
ROUND_DURATION = Gauge("uptime_round_duration_seconds", "How long the last scheduled round took")


class Worker:
    def __init__(self, pool, rdb, client):
        self.pool = pool
        self.rdb = rdb
        self.client = client
        self.stop = threading.Event()
        self.known: set[str] = set()
        self.round_pool = ThreadPoolExecutor(max_workers=10, thread_name_prefix="round")
        self.queue_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="queued")
        self.round_thread: threading.Thread | None = None

    def check_target(self, target: dict) -> None:
        result = checker.check(self.client, target["url"], settings.CHECK_DEADLINE)
        db.record_check(self.pool, target["id"], result.ok, result.status_code, result.latency_ms, result.error)

        name = target["name"]
        TARGET_UP.labels(name).set(1 if result.ok else 0)
        CHECKS.labels(name, "up" if result.ok else "down").inc()
        if result.latency_ms is not None:
            RESPONSE_SECONDS.labels(name).set(result.latency_ms / 1000)
            CHECK_DURATION.labels(name).observe(result.latency_ms / 1000)
        log.info(json.dumps({"target": name, "ok": result.ok, "status": result.status_code,
                             "latency_ms": result.latency_ms, "error": result.error}))

    def run_round(self) -> None:
        started = time.monotonic()
        targets = db.all_targets(self.pool)
        list(self.round_pool.map(self.check_target, targets))

        names = {t["name"] for t in targets}
        for gone in self.known - names:
            for metric in (TARGET_UP, RESPONSE_SECONDS):
                try:
                    metric.remove(gone)
                except KeyError:
                    pass
        self.known = names

        LAST_ROUND.set_to_current_time()
        ROUND_DURATION.set(time.monotonic() - started)
        self.rdb.set(settings.LAST_ROUND_KEY, int(time.time()))

    def safe(self, fn, *args) -> None:
        try:
            fn(*args)
        except Exception as exc:
            log.warning(json.dumps({"error": f"{type(exc).__name__}: {exc}", "in": fn.__name__}))

    def start_round(self) -> None:
        if self.round_thread and self.round_thread.is_alive():
            log.warning(json.dumps({"event": "round_skipped", "reason": "previous round still running"}))
            return
        self.round_thread = threading.Thread(target=self.safe, args=(self.run_round,), name="scheduler")
        self.round_thread.start()

    def handle_queued(self, raw: bytes) -> None:
        target = db.get_target(self.pool, int(raw))
        if target:
            self.check_target(target)

    def heartbeat(self) -> None:
        Path(settings.HEARTBEAT_FILE).touch()

    def run(self) -> None:
        next_round = 0.0
        next_prune = 0.0
        while not self.stop.is_set():
            self.heartbeat()
            now = time.monotonic()
            try:
                if now >= next_round:
                    self.start_round()
                    next_round = now + settings.CHECK_INTERVAL
                if now >= next_prune:
                    removed = db.prune(self.pool, settings.RETENTION_DAYS)
                    if removed:
                        log.info(json.dumps({"pruned_checks": removed}))
                    next_prune = now + 3600
                item = self.rdb.blpop([settings.QUEUE_KEY], timeout=1)
                if item:
                    self.queue_pool.submit(self.safe, self.handle_queued, item[1])
            except (redis.ConnectionError, psycopg.OperationalError, PoolTimeout, OSError) as exc:
                log.warning(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
                self.stop.wait(2)
        if self.round_thread:
            self.round_thread.join(timeout=settings.CHECK_DEADLINE + 2)
        self.queue_pool.shutdown(wait=True, cancel_futures=True)
        self.round_pool.shutdown(wait=True, cancel_futures=True)
        log.info(json.dumps({"event": "stopped"}))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    pool = db.make_pool(settings.DATABASE_URL, max_size=16)
    pool.open(wait=True, timeout=30)
    db.init(pool)
    db.seed(pool, settings.SEED_TARGETS)

    rdb = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=5)
    worker = Worker(pool, rdb, checker.make_client(settings.CHECK_TIMEOUT))

    def shutdown(signum, frame):
        log.info(json.dumps({"event": "shutdown", "signal": signal.Signals(signum).name}))
        worker.stop.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    start_http_server(settings.METRICS_PORT)
    log.info(json.dumps({"event": "started", "interval_s": settings.CHECK_INTERVAL}))
    worker.run()
    pool.close()


if __name__ == "__main__":
    main()
