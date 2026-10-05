import json

import pytest

from aurora.inference.discovery import (
    DiscoveredModelCandidate,
    ModelCandidateStore,
    ModelDiscoveryError,
    ModelDiscoveryService,
    ModelPromotionValidation,
    candidate_id,
)
from aurora.inference.models import ModelSpec
from aurora.inference.providers.registry import ProviderRegistry, ProviderSpec
from aurora.inference.registry import ModelRegistry, ModelRegistryError


class FakeDiscoveryProvider:
    def __init__(self, candidates):
        self.candidates = tuple(candidates)

    def discover(self):
        return self.candidates


class FakeProvider:
    provider_id = "fake"

    def __init__(self, model):
        self.model = model

    def generate(self, request, *, selected_model, execution_id):
        raise AssertionError("provider execution is not part of discovery tests")


def _provider_registry(candidates):
    return ProviderRegistry(
        (
            ProviderSpec(
                "fake",
                "Fake Provider",
                ("chat", "reasoning"),
                True,
                lambda model: FakeProvider(model),
                discovery_factory=lambda: FakeDiscoveryProvider(candidates),
            ),
        )
    )


def _candidate(model_id="fake/model"):
    model = ModelSpec(
        id=model_id,
        provider="fake",
        endpoint="https://example.invalid",
        capabilities=("reasoning",),
        context_window=8192,
        specialties=("research",),
    )
    return DiscoveredModelCandidate(
        candidate_id=candidate_id("fake", model_id),
        provider_id="fake",
        model=model,
        metadata={"object": "model", "owned_by": "test"},
        discovered_at="2026-10-01T13:00:00+00:00",
    )


def test_discovery_stages_candidate_without_activating_model(tmp_path):
    candidate = _candidate()
    provider_registry = _provider_registry([candidate])
    model_registry = ModelRegistry([], provider_registry=provider_registry)
    candidate_store = ModelCandidateStore(tmp_path / "candidates.db")

    service = ModelDiscoveryService(
        provider_registry,
        model_registry,
        candidate_store,
    )

    staged = service.discover_and_stage("fake")

    assert staged[0].state == "discovered"
    assert staged[0].model.enabled is False
    assert staged[0].model.healthy is False
    assert staged[0].model.available is False
    assert "fake/model" not in model_registry.ids
    assert candidate_store.get(candidate.candidate_id) == staged[0]


def test_discovery_requires_provider_discovery_support(tmp_path):
    registry = ProviderRegistry(
        (
            ProviderSpec(
                "fake",
                "Fake Provider",
                ("chat",),
                True,
                lambda model: FakeProvider(model),
            ),
        )
    )
    service = ModelDiscoveryService(
        registry,
        ModelRegistry([]),
        ModelCandidateStore(tmp_path / "candidates.db"),
    )

    with pytest.raises(ModelDiscoveryError, match="does not support model discovery"):
        service.discover_and_stage("fake")


def test_discovery_rejects_candidate_with_wrong_provider_identity(tmp_path):
    candidate = _candidate()
    wrong = DiscoveredModelCandidate(
        candidate_id=candidate.candidate_id,
        provider_id="other",
        model=candidate.model,
        metadata=candidate.metadata,
        discovered_at=candidate.discovered_at,
    )
    service = ModelDiscoveryService(
        _provider_registry([wrong]),
        ModelRegistry([]),
        ModelCandidateStore(tmp_path / "candidates.db"),
    )

    with pytest.raises(ModelDiscoveryError, match="provider identity"):
        service.discover_and_stage("fake")


def test_promotion_requires_all_governance_gates(tmp_path):
    candidate = _candidate()
    provider_registry = _provider_registry([candidate])
    model_registry = ModelRegistry([], provider_registry=provider_registry)
    store = ModelCandidateStore(tmp_path / "candidates.db")
    service = ModelDiscoveryService(provider_registry, model_registry, store)
    service.discover_and_stage("fake")

    with pytest.raises(ModelDiscoveryError, match="policy, security and health"):
        service.promote(
            candidate.candidate_id,
            ModelSpec(
                id="fake/model",
                provider="fake",
                endpoint="https://example.invalid",
                capabilities=("reasoning",),
                context_window=8192,
                enabled=True,
                healthy=True,
                available=True,
            ),
            ModelPromotionValidation(
                policy_verified=True,
                security_verified=False,
                health_verified=True,
            ),
        )

    assert candidate.candidate_id not in {
        item.candidate_id
        for item in store.list(state="synced")
    }
    assert "fake/model" not in model_registry.ids


def test_promotion_registers_only_explicitly_validated_model(tmp_path):
    candidate = _candidate()
    provider_registry = _provider_registry([candidate])
    model_registry = ModelRegistry([], provider_registry=provider_registry)
    store = ModelCandidateStore(tmp_path / "candidates.db")
    service = ModelDiscoveryService(provider_registry, model_registry, store)
    service.discover_and_stage("fake")

    approved = ModelSpec(
        id="fake/model",
        provider="fake",
        endpoint="https://example.invalid",
        capabilities=("reasoning",),
        context_window=8192,
        specialties=("research",),
        enabled=True,
        healthy=True,
        available=True,
    )
    promoted = service.promote(
        candidate.candidate_id,
        approved,
        ModelPromotionValidation(True, True, True, "validated"),
    )

    assert promoted == approved
    assert model_registry.resolve("fake/model") == approved
    assert store.get(candidate.candidate_id).state == "synced"


def test_promotion_does_not_overwrite_existing_model(tmp_path):
    candidate = _candidate()
    provider_registry = _provider_registry([candidate])
    existing = ModelSpec(
        id="fake/model",
        provider="fake",
        endpoint="https://existing.invalid",
        capabilities=("chat",),
    )
    model_registry = ModelRegistry([existing], provider_registry=provider_registry)
    store = ModelCandidateStore(tmp_path / "candidates.db")
    service = ModelDiscoveryService(provider_registry, model_registry, store)
    service.discover_and_stage("fake")

    with pytest.raises(ModelDiscoveryError, match="duplicate model registration"):
        service.promote(
            candidate.candidate_id,
            ModelSpec(
                id="fake/model",
                provider="fake",
                endpoint="https://new.invalid",
                capabilities=("reasoning",),
                enabled=True,
                healthy=True,
                available=True,
            ),
            ModelPromotionValidation(True, True, True),
        )

    assert model_registry.resolve("fake/model") == existing
    assert store.get(candidate.candidate_id).state == "discovered"


def test_candidate_store_filters_by_state(tmp_path):
    store = ModelCandidateStore(tmp_path / "candidates.db")
    first = _candidate("fake/first")
    second = _candidate("fake/second")
    store.upsert(first)
    store.upsert(second)
    store.set_state(first.candidate_id, "rejected")

    assert tuple(item.model.id for item in store.list(state="discovered")) == ("fake/second",)
    assert tuple(item.model.id for item in store.list(state="rejected")) == ("fake/first",)


def test_nvidia_discovery_parses_openai_compatible_catalog(monkeypatch):
    from aurora.inference.providers.nvidia_discovery import NVIDIAModelDiscoveryProvider

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "meta/example-model",
                            "object": "model",
                            "owned_by": "meta",
                            "capabilities": ["chat", "reasoning"],
                            "context_window": 32768,
                        },
                        {"id": "", "object": "model"},
                        {"object": "model"},
                    ],
                }
            ).encode("utf-8")

    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.headers.get("Authorization")
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(
        "aurora.inference.providers.nvidia_discovery.urlopen",
        fake_urlopen,
    )

    candidates = NVIDIAModelDiscoveryProvider(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key="secret-not-in-candidate",
    ).discover()

    assert len(candidates) == 1
    assert candidates[0].model.id == "meta/example-model"
    assert candidates[0].model.enabled is False
    assert candidates[0].model.context_window == 32768
    assert candidates[0].metadata["owned_by"] == "meta"
    assert candidates[0].candidate_id == candidate_id(
        "nvidia_api", "meta/example-model"
    )
    assert captured == {
        "url": "https://integrate.api.nvidia.com/v1/models",
        "authorization": "Bearer secret-not-in-candidate",
        "timeout": 15.0,
    }
    assert "secret-not-in-candidate" not in json.dumps(candidates[0].metadata)


def test_nvidia_discovery_conservatively_excludes_non_conversational_catalog_models(monkeypatch):
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
                    {"id": "nvidia/nemotron-3-super-120b-a12b", "object": "model"},
                    {"id": "nvidia/nemotron-3.5-content-safety", "object": "model"},
                    {"id": "nvidia/nemotron-3-embed-1b", "object": "model"},
                ],
            }).encode("utf-8")

    monkeypatch.setattr(
        "aurora.inference.providers.nvidia_discovery.urlopen",
        lambda _request, timeout: FakeResponse(),
    )

    candidates = NVIDIAModelDiscoveryProvider(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key="secret",
    ).discover()

    by_id = {candidate.model.id: candidate.model for candidate in candidates}
    assert by_id["nvidia/nemotron-3-super-120b-a12b"].capabilities == ("chat", "reasoning")
    assert by_id["nvidia/nemotron-3.5-content-safety"].capabilities == ()
    assert by_id["nvidia/nemotron-3-embed-1b"].capabilities == ()


def test_candidate_id_is_deterministic():
    assert candidate_id("fake", "model") == candidate_id(" fake ", " model ")
    assert candidate_id("fake", "model") != candidate_id("other", "model")
