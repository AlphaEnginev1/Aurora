from aurora.inference.health import check_model
from aurora.inference.models import ModelSpec

def test_disabled_model_is_not_healthy():
    h=check_model(ModelSpec("m","nvidia_api","https://example.invalid/v1",enabled=False), api_key_present=True)
    assert not h.configured and h.reason=="model_disabled"

def test_nvidia_requires_runtime_key():
    h=check_model(ModelSpec("m","nvidia_api","https://example.invalid/v1"), api_key_present=False)
    assert not h.configured and h.reason=="NVIDIA_API_KEY_missing"

def test_configured_model_is_healthy():
    h=check_model(ModelSpec("m","nvidia_api","https://example.invalid/v1"), api_key_present=True)
    assert h.configured
