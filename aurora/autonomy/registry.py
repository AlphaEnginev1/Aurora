"""Policy-controlled capability registry."""

import json
from dataclasses import dataclass
from pathlib import Path


class RegistryError(ValueError):
    """Raised when the tool registry is invalid."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: str
    source: str
    executable: str
    version: str | None = None
    artifact_sha256: str | None = None


def load_registry(path: str | Path) -> dict[str, ToolSpec]:
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RegistryError(f"Unable to load tool registry: {source}") from exc
    tools = data.get("tools")
    if not isinstance(tools, list):
        raise RegistryError("tool registry 'tools' must be a list")
    result: dict[str, ToolSpec] = {}
    for item in tools:
        if not isinstance(item, dict) or not all(
            k in item for k in ("name", "kind", "source", "executable")
        ):
            raise RegistryError(
                "each tool requires name, kind, source, and executable"
            )
        for optional in ("version", "artifact_sha256"):
            if optional in item and item[optional] is not None and not isinstance(
                item[optional], str
            ):
                raise RegistryError(f"tool field '{optional}' must be a string")
        spec = ToolSpec(
            item["name"],
            item["kind"],
            item["source"],
            item["executable"],
            item.get("version"),
            item.get("artifact_sha256"),
        )
        if spec.name in result:
            raise RegistryError(f"duplicate tool: {spec.name}")
        result[spec.name] = spec
    return result
