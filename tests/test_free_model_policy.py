from aurora.inference.free_policy import free_model_ids, is_free_model, record_metadata


def test_curated_free_model_catalog_is_fail_closed():
    assert is_free_model("gemini", "gemini-3.9-future") is False
    assert is_free_model("nvidia_api", "nvidia/nemotron-3-ultra-550b-a55b") is True
    assert is_free_model("nvidia_api", "meta/llama-3.1-8b-instruct") is False
    assert is_free_model("gemini", "gemini-3.7-flash") is False
    assert is_free_model("openai", "gpt-5") is False
    assert is_free_model("anthropic", "claude-opus") is False


def test_free_model_ids_follow_policy_rank():
    ids = free_model_ids("nvidia_api")
    assert ids[:3] == (
        "nvidia/nemotron-3-ultra-550b-a55b",
        "nvidia/nemotron-3-super-120b-a12b",
        "z-ai/glm-5-3",
    )


def test_free_model_metadata_contains_specialties():
    metadata = record_metadata("nvidia_api", "poolside/laguna-xs-2.1")
    assert metadata["tier"] == "free"
    assert "coding" in metadata["specialties"]



def test_free_api_allowlists_are_explicit_and_fail_closed():
    assert is_free_model("groq", "openai/gpt-oss-120b") is True
    assert is_free_model("groq", "openai/gpt-oss-20b") is True
    assert is_free_model("gemini", "gemini-3.8-flash") is True
    assert is_free_model("cloudflare", "@cf/meta/llama-3.3-70b-instruct-fp8-fast") is True
    assert is_free_model("openrouter", "openrouter/free") is True
    assert is_free_model("mistral", "labs-leanstral-2603") is True
    assert is_free_model("groq", "some-paid-model") is False
    assert is_free_model("gemini", "gemini-3.9-future") is False
    assert is_free_model("cloudflare", "@cf/zai-org/glm-5.3") is False


def test_free_api_catalogs_are_ranked():
    assert free_model_ids("groq")[0] == "openai/gpt-oss-120b"
    assert free_model_ids("cloudflare")[0] == "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    assert free_model_ids("mistral") == ("labs-leanstral-2603",)
    assert record_metadata("gemini", "gemini-3.8-flash")["tier"] == "free"



def test_factory_requires_credentials_and_free_tier_confirmation(monkeypatch, tmp_path):
    from pathlib import Path

    from aurora.orchestration.factory import build_orchestration_engine

    for name in (
        "GROQ_API_KEY",
        "GEMINI_API_KEY",
        "CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE_ACCOUNT_ID",
        "MISTRAL_API_KEY",
        "OPENROUTER_API_KEY",
        "AURORA_GROQ_FREE_TIER_CONFIRMED",
        "AURORA_GEMINI_FREE_TIER_CONFIRMED",
        "AURORA_CLOUDFLARE_FREE_TIER_CONFIRMED",
        "AURORA_MISTRAL_FREE_TIER_CONFIRMED",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AURORA_INFERENCE_COST_POLICY", "free_only")
    monkeypatch.setenv("AURORA_WORKSPACE_ROOT", str(tmp_path / "runtime"))
    config = Path(__file__).resolve().parents[1] / "config" / "inference-policy.json"

    # A key alone is not enough to activate a provider whose account could bill.
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-used-for-network")
    engine = build_orchestration_engine(config)
    assert not any(model.provider == "groq" for model in engine._router.model_registry.models)

    monkeypatch.setenv("AURORA_GROQ_FREE_TIER_CONFIRMED", "1")
    engine = build_orchestration_engine(config)
    groq_models = [
        model for model in engine._router.model_registry.models
        if model.provider == "groq" and model.enabled and model.pricing_tier == "free"
    ]
    assert {model.id for model in groq_models} == {
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
    }
