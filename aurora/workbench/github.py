"""Governed GitHub connector for Aurora's Universal Workbench."""
from __future__ import annotations

import base64
import json
import os
from typing import Any
from urllib.parse import quote


class GitHubConnectorError(RuntimeError):
    """Raised when GitHub access cannot be performed safely."""


class GitHubConnector:
    """Token-isolated GitHub REST connector through Aurora's network boundary."""

    def __init__(self, gateway, *, api_base: str = "https://api.github.com") -> None:
        self.gateway = gateway
        self.api_base = api_base.rstrip("/")

    def _token(self) -> str:
        return os.getenv("GITHUB_TOKEN", "").strip() or os.getenv("GH_TOKEN", "").strip()

    @property
    def write_enabled(self) -> bool:
        return os.getenv("AURORA_GITHUB_ALLOW_WRITES", "1").strip().lower() in {"1", "true", "yes"}

    @property
    def merge_enabled(self) -> bool:
        return os.getenv("AURORA_GITHUB_ALLOW_MERGE", "0").strip().lower() in {"1", "true", "yes"}

    def available(self) -> bool:
        return bool(self._token())

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        token = self._token()
        if not token:
            raise GitHubConnectorError(
                "GitHub connector is unavailable: GITHUB_TOKEN or GH_TOKEN is required"
            )
        normalized = path.strip()
        if not normalized.startswith("/"):
            normalized = "/" + normalized
        url = self.api_base + normalized
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        response = self.gateway.request_with_metadata(
            url,
            method=method,
            headers=headers,
            body=body,
        )
        raw = response.body.decode("utf-8")
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise GitHubConnectorError("GitHub response was not valid JSON") from exc

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def create_branch(self, owner: str, repo: str, branch: str, sha: str) -> dict[str, Any]:
        self._require_write("branch creation")
        if not branch.strip() or not sha.strip():
            raise GitHubConnectorError("branch and sha are required")
        return self._as_dict(
            self._request(
                "POST",
                self.repo_path(owner, repo, "/git/refs"),
                {"ref": "refs/heads/" + branch.strip(), "sha": sha.strip()},
            )
        )

    def get_file(self, owner: str, repo: str, path: str, ref: str | None = None) -> dict[str, Any]:
        suffix = "/contents/" + quote(path.strip().lstrip("/"), safe="/")
        if ref:
            suffix += "?ref=" + quote(ref.strip(), safe="")
        return self._as_dict(self.get(self.repo_path(owner, repo, suffix)))

    def upsert_file(
        self,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str,
        sha: str | None = None,
    ) -> dict[str, Any]:
        self._require_write("file write")
        if not path.strip() or not message.strip() or not branch.strip():
            raise GitHubConnectorError("path, message and branch are required")
        payload: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        suffix = "/contents/" + quote(path.strip().lstrip("/"), safe="/")
        return self._as_dict(self._request("PUT", self.repo_path(owner, repo, suffix), payload))

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str = "",
        draft: bool = False,
    ) -> dict[str, Any]:
        self._require_write("pull request creation")
        if not title.strip() or not head.strip() or not base.strip():
            raise GitHubConnectorError("title, head and base are required")
        return self._as_dict(
            self._request(
                "POST",
                self.repo_path(owner, repo, "/pulls"),
                {
                    "title": title,
                    "head": head,
                    "base": base,
                    "body": body,
                    "draft": bool(draft),
                },
            )
        )

    def merge_pull_request(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        method: str = "merge",
        expected_head_sha: str | None = None,
    ) -> dict[str, Any]:
        if not self.merge_enabled:
            raise GitHubConnectorError("GitHub PR merge is disabled by policy")
        payload: dict[str, Any] = {"merge_method": method}
        if expected_head_sha:
            payload["sha"] = expected_head_sha
        return self._as_dict(
            self._request(
                "PUT",
                self.repo_path(owner, repo, f"/pulls/{int(number)}/merge"),
                payload,
            )
        )

    def workflow_runs(self, owner: str, repo: str, *, branch: str | None = None, status: str | None = None) -> dict[str, Any]:
        suffix = "/actions/runs"
        params = []
        if branch:
            params.append("branch=" + quote(branch.strip(), safe=""))
        if status:
            params.append("status=" + quote(status.strip(), safe=""))
        if params:
            suffix += "?" + "&".join(params)
        return self._as_dict(self.get(self.repo_path(owner, repo, suffix)))

    def _require_write(self, operation: str) -> None:
        if not self.write_enabled:
            raise GitHubConnectorError(
                f"GitHub {operation} is disabled by policy; set AURORA_GITHUB_ALLOW_WRITES=1"
            )

    @staticmethod
    def repo_path(owner: str, repo: str, suffix: str = "") -> str:
        owner = quote(owner.strip(), safe="")
        repo = quote(repo.strip(), safe="")
        if not owner or not repo:
            raise GitHubConnectorError("GitHub owner and repository are required")
        return f"/repos/{owner}/{repo}{suffix}"

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {"data": value}
        return value


__all__ = ["GitHubConnector", "GitHubConnectorError"]
