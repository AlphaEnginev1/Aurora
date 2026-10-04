"""Provider-neutral multimodal message contracts.

Aurora Workbench may produce text, images, audio, and video evidence. This
module validates and normalizes those parts without binding the orchestration
layer to a particular model vendor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class MultimodalContentError(ValueError):
    """Raised when a message contains unsupported or malformed media content."""


SUPPORTED_MODALITIES = frozenset({"text", "image", "audio", "video"})


@dataclass(frozen=True)
class MediaPart:
    modality: str
    mime_type: str | None = None
    data: str | None = None
    uri: str | None = None
    text: str | None = None

    def as_gemini_part(self) -> dict[str, Any]:
        if self.modality == "text":
            return {"text": self.text or ""}
        if self.uri:
            return {"fileData": {"mimeType": self.mime_type or "application/octet-stream", "fileUri": self.uri}}
        if self.data:
            return {"inlineData": {"mimeType": self.mime_type or "application/octet-stream", "data": self.data}}
        raise MultimodalContentError(f"{self.modality} part requires data or uri")

    def as_openai_part(self) -> dict[str, Any]:
        if self.modality == "text":
            return {"type": "text", "text": self.text or ""}
        if self.modality == "image":
            url = self.uri or (
                f"data:{self.mime_type or 'application/octet-stream'};base64,{self.data}"
                if self.data
                else None
            )
            if not url:
                raise MultimodalContentError("image part requires data or uri")
            return {"type": "image_url", "image_url": {"url": url}}
        raise MultimodalContentError(
            f"OpenAI-compatible adapter has no provider-neutral encoding for {self.modality}"
        )


def parse_content(content: Any) -> tuple[MediaPart, ...]:
    """Validate one message content value and return normalized parts."""
    if isinstance(content, str):
        return (MediaPart("text", text=content),)
    if not isinstance(content, list):
        raise MultimodalContentError("message content must be a string or an array of content parts")
    parts: list[MediaPart] = []
    for raw in content:
        if not isinstance(raw, Mapping):
            raise MultimodalContentError("each multimodal content part must be an object")
        modality = str(raw.get("modality") or raw.get("type") or "").strip().lower()
        if modality == "input_text":
            modality = "text"
        if modality == "input_image":
            modality = "image"
        if modality not in SUPPORTED_MODALITIES:
            raise MultimodalContentError(f"unsupported multimodal part type: {modality or '<empty>'}")
        text_value = raw.get("text")
        data = raw.get("data")
        uri = raw.get("uri")
        mime_type = raw.get("mime_type") or raw.get("mimeType")
        for name, value in (("text", text_value), ("data", data), ("uri", uri), ("mime_type", mime_type)):
            if value is not None and not isinstance(value, str):
                raise MultimodalContentError(f"{name} must be a string")
        if modality == "text":
            if not isinstance(text_value, str):
                raise MultimodalContentError("text part requires a text string")
            parts.append(MediaPart(modality, text=text_value))
            continue
        if not data and not uri:
            raise MultimodalContentError(f"{modality} part requires data or uri")
        parts.append(MediaPart(modality, mime_type=mime_type, data=data, uri=uri))
    if not parts:
        raise MultimodalContentError("multimodal content array cannot be empty")
    return tuple(parts)


def message_modalities(messages: list[dict[str, Any]]) -> frozenset[str]:
    modalities: set[str] = set()
    for message in messages:
        if not isinstance(message, Mapping):
            raise MultimodalContentError("every message must be an object")
        modalities.update(part.modality for part in parse_content(message.get("content", "")))
    return frozenset(modalities)


def openai_messages(messages: list[dict[str, Any]], *, allowed_modalities: frozenset[str] = SUPPORTED_MODALITIES) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        parts = parse_content(message.get("content", ""))
        for part in parts:
            if part.modality not in allowed_modalities:
                raise MultimodalContentError(f"modality {part.modality} is not supported by this provider")
        content = parts[0].as_openai_part() if len(parts) == 1 and parts[0].modality == "text" else [
            part.as_openai_part() for part in parts
        ]
        result.append({"role": message.get("role"), "content": content})
    return result


__all__ = [
    "MediaPart",
    "MultimodalContentError",
    "SUPPORTED_MODALITIES",
    "message_modalities",
    "openai_messages",
    "parse_content",
]
