from pathlib import Path

from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()
SCHEMA_LOCK = 72_001


class DuplicateName(Exception):
    pass


def make_pool(url: str, max_size: int = 5) -> ConnectionPool:
    return ConnectionPool(
        url,
        min_size=1,
        max_size=max_size,
        kwargs={"row_factory": dict_row},
        check=ConnectionPool.check_connection,
        open=False,
    )


def init(pool: ConnectionPool) -> None:
    # web and worker start at the same time; without the lock both can race on CREATE TABLE
    with pool.connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK,))
        conn.execute(SCHEMA)


def seed(pool: ConnectionPool, spec: str) -> None:
    pairs = [item.split("=", 1) for item in spec.split(",") if "=" in item]
    with pool.connection() as conn:
        for name, url in pairs:
            conn.execute(
                "INSERT INTO targets (name, url) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING",
                (name.strip(), url.strip()),
            )


def ping(pool: ConnectionPool) -> None:
    with pool.connection(timeout=2) as conn:
        conn.execute("SELECT 1")


def all_targets(pool: ConnectionPool) -> list[dict]:
    with pool.connection() as conn:
        return conn.execute("SELECT id, name, url FROM targets ORDER BY id").fetchall()


def get_target(pool: ConnectionPool, target_id: int) -> dict | None:
    with pool.connection() as conn:
        return conn.execute("SELECT id, name, url FROM targets WHERE id = %s", (target_id,)).fetchone()


def add_target(pool: ConnectionPool, name: str, url: str) -> dict:
    try:
        with pool.connection() as conn:
            return conn.execute(
                "INSERT INTO targets (name, url) VALUES (%s, %s) RETURNING id, name, url",
                (name, url),
            ).fetchone()
    except UniqueViolation:
        raise DuplicateName(name)


def delete_target(pool: ConnectionPool, target_id: int) -> bool:
    with pool.connection() as conn:
        return conn.execute("DELETE FROM targets WHERE id = %s", (target_id,)).rowcount == 1


def status(pool: ConnectionPool) -> list[dict]:
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT t.id, t.name, t.url,
                   c.ok, c.status_code, c.latency_ms, c.error, c.checked_at,
                   s.total AS checks_24h,
                   CASE WHEN s.total > 0 THEN round(100.0 * s.up / s.total, 2) END AS uptime_24h
            FROM targets t
            LEFT JOIN LATERAL (
                SELECT ok, status_code, latency_ms, error, checked_at
                FROM checks WHERE target_id = t.id
                ORDER BY checked_at DESC LIMIT 1
            ) c ON true
            LEFT JOIN LATERAL (
                SELECT count(*) AS total, count(*) FILTER (WHERE ok) AS up
                FROM checks
                WHERE target_id = t.id AND checked_at > now() - interval '24 hours'
            ) s ON true
            ORDER BY t.name
            """
        ).fetchall()


def recent_checks(pool: ConnectionPool, target_id: int, limit: int) -> list[dict]:
    with pool.connection() as conn:
        return conn.execute(
            """
            SELECT checked_at, ok, status_code, latency_ms, error
            FROM checks WHERE target_id = %s
            ORDER BY checked_at DESC LIMIT %s
            """,
            (target_id, limit),
        ).fetchall()


def record_check(pool: ConnectionPool, target_id: int, ok: bool, status_code, latency_ms, error) -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO checks (target_id, ok, status_code, latency_ms, error)
            SELECT %s, %s, %s, %s, %s WHERE EXISTS (SELECT 1 FROM targets WHERE id = %s)
            """,
            (target_id, ok, status_code, latency_ms, error, target_id),
        )


def prune(pool: ConnectionPool, days: int) -> int:
    with pool.connection() as conn:
        return conn.execute(
            "DELETE FROM checks WHERE checked_at < now() - make_interval(days => %s)", (days,)
        ).rowcount
