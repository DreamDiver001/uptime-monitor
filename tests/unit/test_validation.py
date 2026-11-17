import pytest
from pydantic import ValidationError

from uptime.web import TargetIn


@pytest.mark.parametrize("url", ["http://example.com", "https://example.com/health?x=1", "http://web:8000/healthz"])
def test_accepts_http_urls(url):
    assert TargetIn(name="svc", url=url).url == url


@pytest.mark.parametrize("url", ["ftp://example.com", "example.com", "http://", "javascript:alert(1)", ""])
def test_rejects_other_urls(url):
    with pytest.raises(ValidationError):
        TargetIn(name="svc", url=url)


def test_name_is_trimmed():
    assert TargetIn(name="  api  ", url="http://x.com").name == "api"


def test_blank_name_rejected():
    with pytest.raises(ValidationError):
        TargetIn(name="   ", url="http://x.com")
