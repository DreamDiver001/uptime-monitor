import httpx

from uptime import checker


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_200_is_up():
    result = checker.check(client_for(lambda req: httpx.Response(200)), "http://svc/")
    assert result.ok
    assert result.status_code == 200
    assert result.latency_ms is not None
    assert result.error is None


def test_redirect_to_ok_page_is_up():
    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"Location": "http://svc/new"})
        return httpx.Response(200)

    result = checker.check(client_for(handler), "http://svc/old")
    assert result.ok
    assert result.status_code == 200


def test_server_error_is_down():
    result = checker.check(client_for(lambda req: httpx.Response(503)), "http://svc/")
    assert not result.ok
    assert result.status_code == 503
    assert result.error == "HTTP 503"


def test_404_is_down():
    result = checker.check(client_for(lambda req: httpx.Response(404)), "http://svc/")
    assert not result.ok
    assert result.error == "HTTP 404"


def test_timeout():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    result = checker.check(client_for(handler), "http://svc/")
    assert not result.ok
    assert result.error == "timeout"
    assert result.status_code is None


def test_connection_refused():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    result = checker.check(client_for(handler), "http://svc/")
    assert result.error == "connection failed"


def test_redirect_loop_is_reported():
    def handler(request):
        return httpx.Response(302, headers={"Location": str(request.url)})

    result = checker.check(client_for(handler), "http://svc/loop")
    assert not result.ok
    assert result.error == "TooManyRedirects"
