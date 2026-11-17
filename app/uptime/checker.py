import time
from dataclasses import dataclass

import httpx

MAX_REDIRECTS = 5


@dataclass
class Result:
    ok: bool
    status_code: int | None = None
    latency_ms: int | None = None
    error: str | None = None


def make_client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers={"User-Agent": "uptime-monitor/1.0"})


def check(client: httpx.Client, url: str, deadline: float = 10.0) -> Result:
    start = time.perf_counter()
    try:
        for _ in range(MAX_REDIRECTS + 1):
            remaining = deadline - (time.perf_counter() - start)
            if remaining <= 0:
                return Result(ok=False, error="timeout")
            per_request = min(client.timeout.read or remaining, remaining)
            response = client.get(url, follow_redirects=False, timeout=per_request)
            if not response.has_redirect_location:
                break
            url = str(response.next_request.url)
        else:
            return Result(ok=False, error="too many redirects")
    except httpx.TimeoutException:
        return Result(ok=False, error="timeout")
    except httpx.ConnectError:
        return Result(ok=False, error="connection failed")
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        return Result(ok=False, error=type(exc).__name__)

    latency = round((time.perf_counter() - start) * 1000)
    if response.status_code >= 400:
        return Result(False, response.status_code, latency, f"HTTP {response.status_code}")
    return Result(True, response.status_code, latency)
