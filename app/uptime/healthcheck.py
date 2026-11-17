import sys
import time
import urllib.request
from pathlib import Path


def http(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return response.status == 200
    except OSError:
        return False


def heartbeat(path: str, max_age: float) -> bool:
    try:
        return time.time() - Path(path).stat().st_mtime < max_age
    except FileNotFoundError:
        return False


if __name__ == "__main__":
    if sys.argv[1] == "heartbeat":
        healthy = heartbeat(sys.argv[2], float(sys.argv[3]))
    else:
        healthy = http(sys.argv[1])
    sys.exit(0 if healthy else 1)
