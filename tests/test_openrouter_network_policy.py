"""Regression guard for OpenRouter through Aurora's default-deny egress proxy."""

import json
from pathlib import Path

import pytest

from aurora.autonomy.egress_proxy import EgressProxyPolicy


ROOT = Path(__file__).parents[1]


def test_openrouter_api_host_is_explicitly_allowed_by_local_egress_policy():
    data = json.loads((ROOT / "config" / "network-policy.json").read_text(encoding="utf-8"))

    assert data["default"] == "deny"
    assert "openrouter.ai" in data["allowed_hosts"]

    policy = EgressProxyPolicy(
        allowed_hosts=frozenset(data["allowed_hosts"]),
        allowed_ports=frozenset(data["allowed_ports"]),
    )
    assert policy.authorize("openrouter.ai:443") == ("openrouter.ai", 443)


def test_openrouter_egress_allowlist_does_not_allow_arbitrary_hosts():
    data = json.loads((ROOT / "config" / "network-policy.json").read_text(encoding="utf-8"))
    policy = EgressProxyPolicy(
        allowed_hosts=frozenset(data["allowed_hosts"]),
        allowed_ports=frozenset(data["allowed_ports"]),
    )

    with pytest.raises(PermissionError, match="destination host is not allowed"):
        policy.authorize("example.com:443")
