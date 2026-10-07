import pytest

from aurora.inference.models import InferenceRequest, ModelSpec
from aurora.inference.multimodal import MultimodalContentError, message_modalities, openai_messages, parse_content
from aurora.inference.router import ModelRouter, ModelRoutingError


def test_parse_content_and_detect_modalities():
    messages = [{
        "role": "user",
        "content": [
            {"modality": "text", "text": "Inspect this"},
            {"modality": "image", "mime_type": "image/png", "data": "ZmFrZQ=="},
        ],
    }]
    parts = parse_content(messages[0]["content"])
    assert parts[0].modality == "text"
    assert parts[1].mime_type == "image/png"
    assert message_modalities(messages) == frozenset({"text", "image"})


def test_malformed_media_fails_closed():
    with pytest.raises(MultimodalContentError):
        parse_content([{"modality": "image", "mime_type": "image/png"}])

    with pytest.raises(MultimodalContentError):
        parse_content([{"modality": "binary", "data": "ZmFrZQ=="}])


def test_router_prefers_model_that_supports_required_media():
    router = ModelRouter([
        ModelSpec("text-only", "nvidia_api", "https://example.invalid", ("reasoning",)),
        ModelSpec("vision", "nvidia_api", "https://example.invalid", ("reasoning", "image")),
    ])
    request = InferenceRequest(
        messages=[{
            "role": "user",
            "content": [
                {"modality": "text", "text": "What is shown?"},
                {"modality": "image", "mime_type": "image/png", "data": "ZmFrZQ=="},
            ],
        }]
    )
    assert router.select(request, capability="reasoning").id == "vision"


def test_router_rejects_explicit_model_without_media_support():
    router = ModelRouter([
        ModelSpec("text-only", "nvidia_api", "https://example.invalid", ("reasoning",)),
    ])
    request = InferenceRequest(
        messages=[{
            "role": "user",
            "content": [{"modality": "image", "mime_type": "image/png", "data": "ZmFrZQ=="}],
        }],
        model="text-only",
    )
    with pytest.raises(ModelRoutingError, match="does not support modalities"):
        router.select(request, capability="reasoning")


def test_openai_translation_preserves_image_as_data_url():
    messages = [{
        "role": "user",
        "content": [
            {"modality": "text", "text": "Analyze it"},
            {"modality": "image", "mime_type": "image/png", "data": "ZmFrZQ=="},
        ],
    }]
    translated = openai_messages(messages, allowed_modalities=frozenset({"text", "image"}))
    assert translated[0]["role"] == "user"
    assert translated[0]["content"][1]["type"] == "image_url"
    assert translated[0]["content"][1]["image_url"]["url"] == "data:image/png;base64,ZmFrZQ=="



def test_openai_translation_uses_plain_string_for_text_only_messages():
    translated = openai_messages(
        [{"role": "user", "content": "Reply with exactly: AURORA_PROVIDER_OK"}],
        allowed_modalities=frozenset({"text", "image"}),
    )
    assert translated == [{
        "role": "user",
        "content": "Reply with exactly: AURORA_PROVIDER_OK",
    }]
