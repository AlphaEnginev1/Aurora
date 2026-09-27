"""Governed web-discovery adapters for evidence-first research."""
from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.parse import quote_plus
from typing import Protocol

from aurora.autonomy.internet import InternetGateway, InternetPolicyError


@dataclass(frozen=True)
class DiscoveredSource:
    url: str
    title: str | None = None
    snippet: str | None = None


class ResearchDiscoveryProvider(Protocol):
    def search(self, objective: str, *, limit: int) -> tuple[DiscoveredSource, ...]:
        ...


class HttpSearchProvider:
    """Search adapter for a governed JSON search endpoint.

    The endpoint must be explicitly authorized by InternetPolicy. The provider
    never carries credentials; authentication belongs to the network boundary.
    Expected response shape: {"results": [{"url": "...", "title": "...",
    "snippet": "..."}]}.
    """

    def __init__(self, gateway: InternetGateway, endpoint: str) -> None:
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
        try:
            payload = self.gateway.get(url)
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InternetPolicyError("search provider returned invalid JSON") from exc
        raw_results = data.get("results")
        if not isinstance(raw_results, list):
            raise InternetPolicyError("search provider response is missing results")
        discovered: list[DiscoveredSource] = []
        seen: set[str] = set()
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
