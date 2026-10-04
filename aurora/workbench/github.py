"""Governed read access to GitHub through Aurora's Workbench."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote


class GitHubConnectorError(RuntimeError):
    """Raised when GitHub access cannot be performed safely."""


class GitHubConnector:
    """Small token-isolated GitHub connector using Aurora's network boundary."""

    def __init__(self, gateway, *, api_base: str = "https://api.github.com") -> None:
        self.gateway = gateway
        self.api_base = api_base.rstrip("/")

    def _token(self) -> str:
        return (
            os.getenv("GITHUB_TOKEN", "").strip()
            or os.getenv("GH_TOKEN", "").strip()
        )

    def available(self) -> bool:
        return bool(self._token())

    def get(self, path: str) -> dict[str, Any]:
        token = self._token()
        if not token:
            raise GitHubConnectorError(
                "GitHub connector is unavailable: GITHUB_TOKEN or GH_TOKEN is required"
            )
        normalized = path.strip()
        if not normalized.startswith("/"):
            normalized = "/" + normalized
        url = self.api_base + normalized
        try:
            self.gateway.authorize(url)
        except Exception as exc:
            raise GitHubConnectorError(f"GitHub URL is blocked by network policy: {url}") from exc
        request = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Aurora-Workbench/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.gateway.timeout) as response:
                payload = response.read()
                content_type = response.headers.get_content_type()
                if content_type != "application/json":
                    raise GitHubConnectorError(
                        f"GitHub returned unexpected content type: {content_type}"
                    )
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise GitHubConnectorError(f"GitHub request failed: {exc}") from exc
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubConnectorError("GitHub response was not valid JSON") from exc
        if not isinstance(decoded, dict):
            return {"data": decoded}
        return decoded

    @staticmethod
    def repo_path(owner: str, repo: str, suffix: str = "") -> str:
        owner = quote(owner.strip(), safe="")
        repo = quote(repo.strip(), safe="")
        if not owner or not repo:
            raise GitHubConnectorError("GitHub owner and repository are required")
        return f"/repos/{owner}/{repo}{suffix}"
