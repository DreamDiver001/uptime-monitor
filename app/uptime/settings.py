import os

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://uptime:uptime@localhost:5432/uptime")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "30"))
CHECK_TIMEOUT = float(os.environ.get("CHECK_TIMEOUT", "5"))
CHECK_DEADLINE = float(os.environ.get("CHECK_DEADLINE", "10"))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "7"))
SEED_TARGETS = os.environ.get("SEED_TARGETS", "")
HEARTBEAT_FILE = os.environ.get("HEARTBEAT_FILE", "/tmp/worker-heartbeat")
METRICS_PORT = int(os.environ.get("METRICS_PORT", "9100"))

QUEUE_KEY = "uptime:queue"
LAST_ROUND_KEY = "uptime:last_round"
