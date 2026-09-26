from aurora.inference.models import InferenceRequest, ModelSpec
from aurora.inference.router import ModelRouter, ModelRoutingError

def test_router_selects_capability_model():
    router = ModelRouter([
        ModelSpec("m1", "nvidia_api", "https://example.invalid/v1", ("coding",)),
        ModelSpec("m2", "nvidia_api", "https://example.invalid/v1", ("vision",)),
    ])
    assert router.select(InferenceRequest(messages=[]), capability="coding").id == "m1"

def test_router_rejects_unknown_capability():
    router = ModelRouter([ModelSpec("m1", "nvidia_api", "https://example.invalid/v1", ("coding",))])
    try:
        router.select(InferenceRequest(messages=[]), capability="vision")
    except ModelRoutingError:
        pass
    else:
        raise AssertionError("expected ModelRoutingError")

def test_router_honors_explicit_model():
    router = ModelRouter([ModelSpec("m1", "nvidia_api", "https://example.invalid/v1")])
    assert router.select(InferenceRequest(messages=[], model="m1")).id == "m1"
