"""Policy-controlled source discovery for autonomous research."""
from __future__ import annotations
import json
import os
from dataclasses import dataclass
from typing import Mapping
from aurora.skills.acquisition import SkillGap

class ResearchDiscoveryError(RuntimeError):
    """Raised when no governed research source is available."""

@dataclass(frozen=True)
class ResearchSourceCatalog:
    by_capability: Mapping[str, tuple[str, ...]]
    default_sources: tuple[str, ...] = ()

    @classmethod
    def from_environment(cls, variable: str = "AURORA_RESEARCH_SOURCE_CATALOG") -> "ResearchSourceCatalog":
        raw=os.getenv(variable,"").strip()
        if not raw:
            return cls({})
        try: data=json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ResearchDiscoveryError("AURORA_RESEARCH_SOURCE_CATALOG is invalid JSON") from exc
        if not isinstance(data,dict):
            raise ResearchDiscoveryError("research source catalog must be an object")
        by={}
        for key,value in data.items():
            if not isinstance(key,str) or not isinstance(value,list) or not all(isinstance(url,str) and url.strip() for url in value):
                raise ResearchDiscoveryError("research source catalog entries must be string URL lists")
            by[key]=tuple(dict.fromkeys(value))
        return cls(by_capability=by, default_sources=tuple(by.get("*",())))

    def discover(self, gap: SkillGap) -> tuple[str, ...]:
        urls=list(self.default_sources)
        for capability in gap.required_capabilities:
            urls.extend(self.by_capability.get(capability, ()))
        urls.extend(self.by_capability.get(gap.skill_id, ()))
        result=tuple(dict.fromkeys(urls))
        if not result:
            raise ResearchDiscoveryError(
                f"no governed research source configured for skill {gap.skill_id}"
            )
        return result

__all__=["ResearchDiscoveryError","ResearchSourceCatalog"]


from urllib.parse import quote_plus
from typing import Protocol


@dataclass(frozen=True)
class DiscoveredSource:
    url: str
    title: str | None = None
    snippet: str | None = None


class ResearchDiscoveryProvider(Protocol):
    def search(self, objective: str, *, limit: int) -> tuple[DiscoveredSource, ...]:
        ...


class HttpSearchProvider:
    """Search adapter for an explicitly authorized JSON search endpoint."""

    def __init__(self, gateway, endpoint: str) -> None:
        if not endpoint.strip():
            raise ValueError("search endpoint is required")
        self.gateway = gateway
        self.endpoint = endpoint

    def search(self, objective: str, *, limit: int = 10) -> tuple[DiscoveredSource, ...]:
        if not objective.strip():
            raise ValueError("search objective is required")
        if limit < 1:
            raise ValueError("search limit must be positive")
        separator = "&" if "?" in self.endpoint else "?"
        url = f"{self.endpoint}{separator}q={quote_plus(objective)}&limit={limit}"
        payload = self.gateway.get(url)
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResearchDiscoveryError("search provider returned invalid JSON") from exc
        raw_results = data.get("results")
        if not isinstance(raw_results, list):
            raise ResearchDiscoveryError("search provider response is missing results")
        discovered = []
        seen = set()
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            source_url = item.get("url")
            if not isinstance(source_url, str) or not source_url.strip():
                continue
            source_url = source_url.strip()
            if source_url in seen:
                continue
            seen.add(source_url)
            discovered.append(
                DiscoveredSource(
                    url=source_url,
                    title=item.get("title") if isinstance(item.get("title"), str) else None,
                    snippet=item.get("snippet") if isinstance(item.get("snippet"), str) else None,
                )
            )
            if len(discovered) >= limit:
                break
        return tuple(discovered)


__all__ = [
    "ResearchDiscoveryError",
    "ResearchSourceCatalog",
    "DiscoveredSource",
    "ResearchDiscoveryProvider",
    "HttpSearchProvider",
]
