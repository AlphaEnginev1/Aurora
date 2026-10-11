"""Provider HTTP status instrumentation for external rate alerts."""
import urllib.error

import pytest

from aurora.inference.models import InferenceRequest, ModelSpec
from aurora.inference.providers import nvidia_api


@pytest.mark.parametrize("status", [403, 429])
def test_nvidia_403_429_are_recorded_for_rate_monitoring(monkeypatch, status):
    observed = []
    monkeypatch.setattr(nvidia_api, "report_failure", lambda component, error: observed.append((component, str(error))))
    monkeypatch.setattr(
        nvidia_api.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            urllib.error.HTTPError("https://example.invalid", status, "provider failure", {}, None)
        ),
    )
    provider = nvidia_api.NVIDIAAPIProvider(
        ModelSpec(id="nvidia/test-model", provider="nvidia_api",
                  endpoint="https://integrate.api.nvidia.com/v1"),
        api_key="test-key",
    )
    with pytest.raises(nvidia_api.ProviderHTTPError):
        provider.generate(InferenceRequest(messages=[{"role": "user", "content": "ping"}]))
    assert observed == [("provider_http_403_429", f"NVIDIA HTTP status {status}")]


def test_nvidia_other_http_status_does_not_count_as_403_429(monkeypatch):
    observed = []
    monkeypatch.setattr(nvidia_api, "report_failure", lambda component, error: observed.append(component))
    monkeypatch.setattr(
        nvidia_api.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            urllib.error.HTTPError("https://example.invalid", 500, "server error", {}, None)
        ),
    )
    provider = nvidia_api.NVIDIAAPIProvider(
        ModelSpec(id="nvidia/test-model", provider="nvidia_api",
                  endpoint="https://integrate.api.nvidia.com/v1"),
        api_key="test-key",
    )
    with pytest.raises(nvidia_api.ProviderHTTPError):
        provider.generate(InferenceRequest(messages=[{"role": "user", "content": "ping"}]))
    assert observed == []
