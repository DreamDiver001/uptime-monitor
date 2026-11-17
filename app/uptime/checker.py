import time
from dataclasses import dataclass

import httpx


@dataclass
class Result:
    ok: bool
    status_code: int | None = None
    latency_ms: int | None = None
    error: str | None = None


def make_client(timeout: float) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "uptime-monitor/1.0"},
    )


def check(client: httpx.Client, url: str) -> Result:
    start = time.perf_counter()
    try:
        response = client.get(url)
    except httpx.TimeoutException:
        return Result(ok=False, error="timeout")
    except httpx.ConnectError:
        return Result(ok=False, error="connection failed")
    except httpx.HTTPError as exc:
        return Result(ok=False, error=type(exc).__name__)

    latency = round((time.perf_counter() - start) * 1000)
    if response.status_code >= 400:
        return Result(False, response.status_code, latency, f"HTTP {response.status_code}")
    return Result(True, response.status_code, latency)
