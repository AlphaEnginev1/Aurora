from aurora.inference.free_policy import free_model_ids, is_free_model, record_metadata


def test_curated_free_model_catalog_is_fail_closed():
    assert is_free_model("gemini", "gemini-3.8-flash") is True
    assert is_free_model("gemini", "gemini-3.9-future") is False
    assert is_free_model("nvidia_api", "nvidia/nemotron-3-ultra-550b-a55b") is True
    assert is_free_model("nvidia_api", "meta/llama-3.1-8b-instruct") is False
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
