import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import redis
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field, field_validator

from uptime import db, settings

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency", ["method", "route"])


class TargetIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    url: str = Field(max_length=500)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name is empty")
        return value

    @field_validator("url")
    @classmethod
    def http_url(cls, value: str) -> str:
        value = value.strip()
        parts = urlparse(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("url must start with http:// or https:// and include a host")
        return value


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = db.make_pool(settings.DATABASE_URL)
    pool.open(wait=True, timeout=30)
    db.init(pool)
    db.seed(pool, settings.SEED_TARGETS)
    app.state.pool = pool
    app.state.redis = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=2)
    yield
    pool.close()


app = FastAPI(title="Uptime Monitor", lifespan=lifespan)


@app.middleware("http")
async def metrics(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    path = route.path if route else "unmatched"
    if path not in ("/metrics", "/healthz", "/readyz"):
        REQUESTS.labels(request.method, path, response.status_code).inc()
        LATENCY.labels(request.method, path).observe(time.perf_counter() - start)
    return response


def enqueue(target_id: int) -> None:
    try:
        app.state.redis.rpush(settings.QUEUE_KEY, target_id)
    except redis.RedisError:
        pass


def last_round_age() -> int | None:
    try:
        value = app.state.redis.get(settings.LAST_ROUND_KEY)
    except redis.RedisError:
        return None
    return int(time.time()) - int(value) if value else None


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    targets = db.status(app.state.pool)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "targets": targets,
            "down": sum(1 for t in targets if t["ok"] is False),
            "last_round": last_round_age(),
            "interval": settings.CHECK_INTERVAL,
        },
    )


@app.get("/api/targets")
def list_targets():
    return db.status(app.state.pool)


@app.post("/api/targets", status_code=201)
def create_target(target: TargetIn):
    try:
        row = db.add_target(app.state.pool, target.name, target.url)
    except db.DuplicateName:
        raise HTTPException(409, f"a target named {target.name!r} already exists")
    enqueue(row["id"])
    return row


@app.delete("/api/targets/{target_id}", status_code=204)
def remove_target(target_id: int):
    if not db.delete_target(app.state.pool, target_id):
        raise HTTPException(404, "target not found")
    return Response(status_code=204)


@app.get("/api/targets/{target_id}/checks")
def target_checks(target_id: int, limit: int = 50):
    if not db.get_target(app.state.pool, target_id):
        raise HTTPException(404, "target not found")
    return db.recent_checks(app.state.pool, target_id, max(1, min(limit, 500)))


@app.post("/api/targets/{target_id}/check", status_code=202)
def check_now(target_id: int):
    if not db.get_target(app.state.pool, target_id):
        raise HTTPException(404, "target not found")
    enqueue(target_id)
    return {"queued": target_id}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    problems = {}
    try:
        db.ping(app.state.pool)
    except Exception as exc:
        problems["database"] = type(exc).__name__
    try:
        app.state.redis.ping()
    except redis.RedisError as exc:
        problems["redis"] = type(exc).__name__
    if problems:
        raise HTTPException(503, problems)
    return {"status": "ready"}


@app.get("/metrics")
def metrics_endpoint():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
