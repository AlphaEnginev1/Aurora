"""Opt-in real inference checks for Aurora's free API provider set.

Set AURORA_FREE_API_LIVE=1 only after verifying that the selected account/key
cannot incur charges. These tests never run live API calls by default.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from aurora.orchestration.factory import build_orchestration_engine


MODEL_BY_PROVIDER = {
    "groq": "openai/gpt-oss-120b",
    "gemini": "gemini-3.8-flash",
    "cloudflare": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    "mistral": "labs-leanstral-2603",
    "openrouter": "openrouter/free",
}


@pytest.mark.live
def test_live_free_api_provider_returns_real_inference(tmp_path, monkeypatch):
    if os.getenv("AURORA_FREE_API_LIVE") != "1":
        pytest.skip("set AURORA_FREE_API_LIVE=1 after verifying free-only account billing")

    provider_id = os.getenv("AURORA_FREE_API_PROVIDER", "").strip().lower()
    if provider_id not in MODEL_BY_PROVIDER:
        pytest.fail("AURORA_FREE_API_PROVIDER must name an explicitly supported free API provider")

    confirmation = {
        "groq": "AURORA_GROQ_FREE_TIER_CONFIRMED",
        "gemini": "AURORA_GEMINI_FREE_TIER_CONFIRMED",
        "cloudflare": "AURORA_CLOUDFLARE_FREE_TIER_CONFIRMED",
        "mistral": "AURORA_MISTRAL_FREE_TIER_CONFIRMED",
    }.get(provider_id)
    if confirmation and os.getenv(confirmation) != "1":
        pytest.fail(f"{confirmation}=1 is required for this live provider test")

    key_env = {
        "groq": "GROQ_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "cloudflare": "CLOUDFLARE_API_TOKEN",
        "mistral": "MISTRAL_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }[provider_id]
    if not os.getenv(key_env, "").strip():
        pytest.fail(f"{key_env} is required when AURORA_FREE_API_LIVE=1")
    if provider_id == "cloudflare" and not os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip():
        pytest.fail("CLOUDFLARE_ACCOUNT_ID is required for Cloudflare Workers AI")

    monkeypatch.setenv("AURORA_INFERENCE_COST_POLICY", "free_only")
    monkeypatch.setenv("AURORA_WORKSPACE_ROOT", str(tmp_path / "runtime"))
    policy_path = Path(__file__).resolve().parents[2] / "config" / "inference-policy.json"
    engine = build_orchestration_engine(policy_path)
    model_id = os.getenv("AURORA_FREE_API_MODEL", MODEL_BY_PROVIDER[provider_id]).strip()

    result = engine.run_task(
        task_id="live-free-api-smoke",
        role="live-provider-verification",
        objective="Reply with exactly: AURORA_FREE_API_OK",
        capability="coding",
        model=model_id,
        use_workbench=False,
    )

    assert result.response.provider == provider_id
    assert result.response.model == model_id
    assert "AURORA_FREE_API_OK" in result.response.content
    assert result.response.telemetry is not None
    assert result.response.telemetry.success is True
