import pytest

from aurora.autonomy.internet import InternetGateway, InternetPolicy, InternetPolicyError

def test_gateway_allows_approved_https_host():
    gateway = InternetGateway(InternetPolicy(frozenset({"https"}), frozenset({"example.com"})))
    gateway.authorize("https://example.com/resource")

def test_gateway_rejects_http():
    gateway = InternetGateway(InternetPolicy(frozenset({"https"}), frozenset({"example.com"})))
    with pytest.raises(InternetPolicyError):
        gateway.authorize("http://example.com/resource")

def test_gateway_rejects_unknown_host():
    gateway = InternetGateway(InternetPolicy(frozenset({"https"}), frozenset({"example.com"})))
    with pytest.raises(InternetPolicyError):
        gateway.authorize("https://evil.example/resource")

def test_gateway_rejects_embedded_credentials():
    gateway = InternetGateway(InternetPolicy(frozenset({"https"}), frozenset({"example.com"})))
    with pytest.raises(InternetPolicyError):
        gateway.authorize("https://user:pass@example.com/resource")

def test_gateway_redirect_handler_authorizes_every_redirect_target():
    gateway = InternetGateway(InternetPolicy(frozenset({"https"}), frozenset({"example.com"})))
    from aurora.autonomy.internet import _PolicyRedirectHandler
    handler = _PolicyRedirectHandler(gateway)
    request = type("Req", (), {"full_url": "https://example.com/start"})()
    with pytest.raises(InternetPolicyError):
        handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/final")
    assert handler.hops == []
