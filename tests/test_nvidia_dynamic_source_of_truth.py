import json

import pytest


def test_nvidia_seed_model_is_not_currently_free_without_catalog_confirmation(tmp_path):
    from aurora.inference.free_policy import is_discovered_free_model

    policy = {
        "mode": "free_only",
        "providers": {
            "nvidia_api": {
                "dynamic_catalog": "free_endpoint",
                "models": {
                    "stale/model": {"tier": "free", "rank": 1},
                },
            }
        },
    }
    path = tmp_path / "free-policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")

    assert not is_discovered_free_model(
        "nvidia_api",
        "stale/model",
        discovered_free_endpoint=False,
        path=path,
    )
    assert is_discovered_free_model(
        "nvidia_api",
        "stale/model",
        discovered_free_endpoint=True,
        path=path,
    )


def test_nvidia_discovery_fails_closed_when_official_free_catalog_is_unavailable(monkeypatch):
    from aurora.inference.providers.nvidia_discovery import (
        NVIDIAModelDiscoveryError,
        NVIDIAModelDiscoveryProvider,
    )

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({
                "object": "list",
                "data": [
                    {
                        "id": "stale/model",
                        "capabilities": ["chat", "reasoning"],
                    }
                ],
            }).encode("utf-8")

    monkeypatch.setattr(
        "aurora.inference.providers.nvidia_discovery.urlopen",
        lambda _request, timeout: FakeResponse(),
    )
    monkeypatch.setattr(
        "aurora.inference.providers.nvidia_free_catalog.urlopen",
        lambda _request, timeout: (_ for _ in ()).throw(OSError("catalog unavailable")),
    )

    with pytest.raises(NVIDIAModelDiscoveryError, match="Free Endpoint catalog"):
        NVIDIAModelDiscoveryProvider(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key="test-key",
        ).discover()


def test_nvidia_discovery_uses_current_catalog_not_row_or_seed_metadata(monkeypatch):
    from aurora.inference.providers.nvidia_discovery import NVIDIAModelDiscoveryProvider

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({
                "object": "list",
                "data": [
                    {
                        "id": "current/free-model",
                        "capabilities": ["chat", "reasoning"],
                        "free_endpoint": False,
                        "pricing_tier": "paid",
                    },
                    {
                        "id": "served-but-not-free",
                        "capabilities": ["chat", "reasoning"],
                        "free_endpoint": True,
                    },
                ],
            }).encode("utf-8")

    monkeypatch.setattr(
        "aurora.inference.providers.nvidia_discovery.urlopen",
        lambda _request, timeout: FakeResponse(),
    )
    monkeypatch.setattr(
        "aurora.inference.providers.nvidia_free_catalog.NVIDIAFreeEndpointCatalogProvider.discover",
        lambda self: ("current/free-model",),
    )

    candidates = NVIDIAModelDiscoveryProvider(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key="test-key",
    ).discover()
    by_id = {item.model.id: item.model for item in candidates}

    assert by_id["current/free-model"].pricing_tier == "free"
    assert by_id["served-but-not-free"].pricing_tier == "unknown"
