import urllib.error
import urllib.request

from iped_mcp import netcheck as nc


def test_407_is_reported_as_proxy_login(monkeypatch):
    def boom(*a, **k):
        raise OSError("Tunnel connection failed: 407 Proxy Authentication Required")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    ok, why = nc.check_host("https://ollama.com")
    assert not ok and "407" in why
    text = nc.summary([("Ollama", ok, why)])
    assert "proxy" in text.lower() and "pip" in text


def test_any_http_answer_counts_as_reachable(monkeypatch):
    def deny(*a, **k):
        raise urllib.error.HTTPError("u", 403, "no", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", deny)
    assert nc.check_host("https://github.com")[0]
    assert nc.summary([("x", True, "ok")]).startswith("Internet: ok")
