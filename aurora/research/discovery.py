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
