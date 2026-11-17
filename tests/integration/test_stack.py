import os
import time
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080")
INTERNAL_URL = os.environ.get("INTERNAL_URL", "http://web:8000")
PROM_URL = os.environ.get("PROM_URL")


@pytest.fixture(scope="module")
def api():
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        yield client


@pytest.fixture
def make_target(api):
    created = []

    def make(path):
        name = f"test-{uuid.uuid4().hex[:8]}"
        response = api.post("/api/targets", json={"name": name, "url": INTERNAL_URL + path})
        assert response.status_code == 201, response.text
        created.append(response.json()["id"])
        return response.json()

    yield make
    for target_id in created:
        api.delete(f"/api/targets/{target_id}")


def wait_for_check(api, target_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        checks = api.get(f"/api/targets/{target_id}/checks").json()
        if checks:
            return checks[0]
        time.sleep(0.5)
    pytest.fail(f"target {target_id} was not checked within {timeout} s")


def test_ready(api):
    assert api.get("/readyz").json() == {"status": "ready"}


def test_status_page(api):
    response = api.get("/")
    assert response.status_code == 200
    assert "Uptime Monitor" in response.text


def test_new_target_is_checked_right_away(api, make_target):
    target = make_target("/healthz")
    check = wait_for_check(api, target["id"])
    assert check["ok"] is True
    assert check["status_code"] == 200


def test_failing_target_is_reported_down(api, make_target):
    target = make_target("/does-not-exist")
    check = wait_for_check(api, target["id"])
    assert check["ok"] is False
    assert check["error"] == "HTTP 404"


def test_duplicate_name_rejected(api, make_target):
    target = make_target("/healthz")
    response = api.post("/api/targets", json={"name": target["name"], "url": "http://example.com"})
    assert response.status_code == 409


def test_invalid_url_rejected(api):
    response = api.post("/api/targets", json={"name": "bad", "url": "ftp://example.com"})
    assert response.status_code == 422


def test_delete(api, make_target):
    target = make_target("/healthz")
    assert api.delete(f"/api/targets/{target['id']}").status_code == 204
    assert api.get(f"/api/targets/{target['id']}/checks").status_code == 404


@pytest.mark.skipif(not os.environ.get("BEHIND_NGINX"), reason="only meaningful through nginx")
def test_metrics_not_public(api):
    assert api.get("/metrics").status_code == 404


@pytest.mark.skipif(not PROM_URL, reason="PROM_URL not set")
def test_prometheus_sees_target(api, make_target):
    target = make_target("/healthz")
    wait_for_check(api, target["id"])
    query = f'uptime_target_up{{target="{target["name"]}"}}'
    deadline = time.time() + 60
    while time.time() < deadline:
        result = httpx.get(f"{PROM_URL}/api/v1/query", params={"query": query}).json()["data"]["result"]
        if result:
            assert result[0]["value"][1] == "1"
            return
        time.sleep(2)
    pytest.fail("Prometheus never scraped the target's metric")


@pytest.mark.skipif(not PROM_URL, reason="PROM_URL not set")
def test_prometheus_scrapes_all_jobs():
    expected = {"web": "up", "worker": "up", "prometheus": "up"}
    deadline = time.time() + 45
    while time.time() < deadline:
        targets = httpx.get(f"{PROM_URL}/api/v1/targets").json()["data"]["activeTargets"]
        health = {t["labels"]["job"]: t["health"] for t in targets}
        if health == expected:
            return
        time.sleep(3)
    assert health == expected
