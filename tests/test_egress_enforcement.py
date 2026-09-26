import pytest

from aurora.autonomy.egress_proxy import EgressProxyPolicy


def test_proxy_policy_allows_approved_https_host():
    p = EgressProxyPolicy(frozenset({"github.com"}))
    assert p.authorize("github.com:443") == ("github.com", 443)


def test_proxy_policy_rejects_unapproved_host_and_port():
    p = EgressProxyPolicy(frozenset({"github.com"}))
    for value in ("example.com:443", "github.com:80"):
        with pytest.raises(PermissionError):
            p.authorize(value)


def test_proxy_policy_rejects_ip_literal():
    with pytest.raises(PermissionError):
        EgressProxyPolicy(frozenset({"github.com"})).authorize("1.1.1.1:443")


def test_proxy_policy_rejects_non_public_resolution(monkeypatch):
    p = EgressProxyPolicy(frozenset({"github.com"}))
    monkeypatch.setattr(
        "aurora.autonomy.egress_proxy.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("127.0.0.1", 443)),
            (2, 1, 6, "", ("10.0.0.1", 443)),
        ],
    )
    with pytest.raises(PermissionError, match="non-public"):
        p.resolve_safe("github.com", 443)

