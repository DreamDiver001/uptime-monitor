# Uptime Monitor

Checks a list of websites and APIs on a schedule, stores every result in Postgres, and shows the current status on a web page and in Grafana. Prometheus alerts when a service has been down for more than a minute.

It runs as seven containers with Docker Compose:

| Service | Image | What it does |
|---|---|---|
| nginx | nginx:1.27-alpine | Reverse proxy, the only entry point for the app (port 8080). Blocks `/metrics` from outside. |
| web | built from `Dockerfile` | FastAPI app: status page, REST API, `/healthz`, `/readyz`, `/metrics` |
| worker | same image | Checks every target every 30 s, plus on-demand checks from the Redis queue. Metrics on port 9100. |
| db | postgres:17-alpine | Targets and check history, on the `pgdata` volume |
| redis | redis:7-alpine | Queue for "check now" requests, and the time of the last round |
| prometheus | prom/prometheus:v3.15.1 | Scrapes web and worker every 15 s, evaluates `prometheus/alerts.yml` |
| grafana | grafana/grafana:13.2.6 | Dashboard, provisioned from `grafana/` |

```
browser ──► nginx :8080 ──► web ──┬──► db
                                  └──► redis ◄── worker ──► the monitored sites
            prometheus :9090 ──scrapes──► web, worker
            grafana :3000 ──queries──► prometheus
```

`db` and `redis` publish no ports, so they can only be reached from inside the `backend` network. nginx is only on the `frontend` network and can't reach them at all.

## Run it

Needs Docker Desktop (Windows/macOS) or Docker Engine with the Compose plugin.

```
cp .env.example .env          # PowerShell: copy .env.example .env
```

Set your own passwords in `.env`, then:

```
docker compose up -d --build --wait
```

- Status page: http://localhost:8080
- Grafana: http://localhost:3000 (anyone can view; log in as `admin` with `GRAFANA_PASSWORD` to edit)
- Prometheus: http://localhost:9090, alerts under **Alerts**

The first build takes a few minutes. After that, `docker compose up -d` starts everything in seconds.

```

## API

| Method | Path | |
|---|---|---|
| GET | `/api/targets` | All targets with their last check and 24 h uptime |
| POST | `/api/targets` | `{"name", "url"}`, returns 201, or 409 if the name exists, 422 for a bad URL |
| DELETE | `/api/targets/{id}` | 204, or 404 |
| GET | `/api/targets/{id}/checks?limit=50` | Check history, newest first |
| POST | `/api/targets/{id}/check` | Queue an immediate check, returns 202 |
| GET | `/healthz` | The process is running |
| GET | `/readyz` | Postgres and Redis are reachable, otherwise 503 with the reason |

## How it handles failure

- Containers start in order: `web` and `worker` wait until Postgres and Redis pass their healthchecks, and nginx waits for `web`.
- `web` and `worker` run as a non-root user (UID 10001) on a read-only filesystem. Only `/tmp` is writable.
- The worker writes a heartbeat file on every loop. Its healthcheck fails if the file is older than 60 s, which catches a stuck loop even when the process is still running.
- If Postgres or Redis restarts, the worker logs the error and keeps retrying instead of crashing. The web app's connection pool tests each connection before use, so it doesn't hand out one that died during the restart.
- `docker compose stop` sends SIGTERM. The worker finishes the current check and exits cleanly.
- Both processes create the schema at startup. A Postgres advisory lock stops them from racing each other on `CREATE TABLE`.
- Check results older than 7 days are deleted every hour.

## Tests

```
python -m venv .venv
.venv\Scripts\activate                    # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt pytest
pytest tests/unit                         # no services needed
```

With the stack running:

```
$env:BEHIND_NGINX="1"; $env:PROM_URL="http://localhost:9090"; pytest tests/integration
```

GitHub Actions (`.github/workflows/ci.yml`) runs the unit tests, validates the Compose and Prometheus config, builds and starts the whole stack, and runs the integration tests through nginx. It also checks that data survives `docker compose down` and `up`, that the worker recovers after Postgres restarts, and that Grafana loaded the dashboard.
