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


class CapabilityRegistryStore:
    """Durable store for capabilities discovered at runtime.

    The dynamic registry is kept separate from the static policy registry so
    discovery never rewrites the operator-managed baseline configuration.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def register(self, spec: ToolSpec) -> None:
        if not isinstance(spec, ToolSpec):
            raise RegistryError("dynamic registry accepts only ToolSpec values")
        current = load_registry(self.path) if self.path.exists() else {}
        current[spec.name] = spec
        payload = {
            "tools": [
                {
                    "name": item.name,
                    "kind": item.kind,
                    "source": item.source,
                    "executable": item.executable,
                    "version": item.version,
                    "artifact_sha256": item.artifact_sha256,
                }
                for item in sorted(current.values(), key=lambda item: item.name)
            ]
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.path)


def merge_registries(
    static_path: str | Path,
    dynamic_path: str | Path | None = None,
) -> dict[str, ToolSpec]:
    """Load the operator registry and overlay previously discovered capabilities."""
    result = load_registry(static_path)
    if dynamic_path is not None and Path(dynamic_path).exists():
        result.update(load_registry(dynamic_path))
    return result
