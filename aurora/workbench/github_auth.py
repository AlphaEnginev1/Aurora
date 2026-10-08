"""Local GitHub account connection using the GitHub OAuth web flow."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import urlencode

from aurora.autonomy.internet import InternetPolicyError


class GitHubOAuthError(RuntimeError):
    """Raised when the GitHub account connection cannot be completed safely."""


class GitHubOAuthService:
    """Connect Aurora to the operator's GitHub account without exposing tokens to models."""

    AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
    TOKEN_URL = "https://github.com/login/oauth/access_token"
    USER_URL = "https://api.github.com/user"

    def __init__(self, store, gateway, *, runtime_root: str | Path) -> None:
        self.store = store
        self.gateway = gateway
        self.runtime_root = Path(runtime_root)
        self.state_path = self.runtime_root / "github-oauth-state.json"
        self._lock = RLock()

    @property
    def client_id(self) -> str:
        return os.getenv("AURORA_GITHUB_OAUTH_CLIENT_ID", "").strip()

    @property
    def client_secret(self) -> str:
        return os.getenv("AURORA_GITHUB_OAUTH_CLIENT_SECRET", "").strip()

    @property
    def redirect_uri(self) -> str:
        return os.getenv(
            "AURORA_GITHUB_OAUTH_REDIRECT_URI",
            "http://127.0.0.1:8787/api/github/oauth/callback",
        ).strip()

    @property
    def scopes(self) -> str:
        return os.getenv("AURORA_GITHUB_OAUTH_SCOPES", "repo workflow read:user").strip()

    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)

    def public(self) -> dict[str, Any]:
        data = self.store.public()
        github = data.get("github", {})
        if not isinstance(github, dict):
            github = {}
        return {
            "configured": self.configured(),
            "connected": bool(github.get("access_token_present")),
            "login": github.get("login"),
            "name": github.get("name"),
            "avatar_url": github.get("avatar_url"),
            "scopes": github.get("scopes", []),
            "connected_at": github.get("connected_at"),
        }

    def authorization_url(self) -> str:
        if not self.configured():
            raise GitHubOAuthError(
                "GitHub OAuth is not configured: set AURORA_GITHUB_OAUTH_CLIENT_ID and "
                "AURORA_GITHUB_OAUTH_CLIENT_SECRET"
            )
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        state = secrets.token_urlsafe(32)
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self.state_path.write_text(
                json.dumps(
                    {
                        "state": state,
                        "code_verifier": verifier,
                        "redirect_uri": self.redirect_uri,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            _chmod_private(self.state_path)
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scopes,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "allow_signup": "false",
        }
        return self.AUTHORIZE_URL + "?" + urlencode(params)

    def callback(self, code: str, state: str) -> dict[str, Any]:
        code = str(code).strip()
        state = str(state).strip()
        if not code or not state:
            raise GitHubOAuthError("GitHub OAuth callback is missing code or state")
        with self._lock:
            try:
                payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GitHubOAuthError("GitHub OAuth state is unavailable or expired") from exc
            if not isinstance(payload, dict) or secrets.compare_digest(
                state, str(payload.get("state", ""))
            ) is False:
                raise GitHubOAuthError("GitHub OAuth state validation failed")
            verifier = str(payload.get("code_verifier", ""))
            redirect_uri = str(payload.get("redirect_uri", self.redirect_uri))
            self.state_path.unlink(missing_ok=True)

        token = self._exchange_code(code, verifier, redirect_uri)
        identity = self._github_request(
            self.USER_URL,
            headers={"Authorization": f"Bearer {token}"},
        )
        if not isinstance(identity, dict) or not identity.get("login"):
            raise GitHubOAuthError("GitHub did not return a valid user identity")

        raw_scopes = str(token.get("scope", "") or "")
        scopes = [item for item in raw_scopes.split(",") if item]
        self.store.set_github_connection(
            access_token=str(token["access_token"]),
            login=str(identity.get("login")),
            name=str(identity.get("name") or ""),
            avatar_url=str(identity.get("avatar_url") or ""),
            scopes=scopes,
        )
        os.environ["GITHUB_TOKEN"] = str(token["access_token"])
        os.environ["GH_TOKEN"] = str(token["access_token"])
        return self.public()

    def disconnect(self) -> None:
        self.store.clear_github_connection()
        os.environ.pop("GITHUB_TOKEN", None)
        os.environ.pop("GH_TOKEN", None)

    def restore_runtime_token(self) -> bool:
        token = self.store.credential("github")
        if not token:
            return False
        os.environ["GITHUB_TOKEN"] = token
        os.environ["GH_TOKEN"] = token
        return True

    def _exchange_code(self, code: str, verifier: str, redirect_uri: str) -> dict[str, Any]:
        body = urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            }
        ).encode("utf-8")
        response = self.gateway.request_with_metadata(
            self.TOKEN_URL,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body=body,
        )
        try:
            data = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubOAuthError("GitHub token response was not valid JSON") from exc
        if not isinstance(data, dict) or not data.get("access_token"):
            message = str(data.get("error_description") or data.get("error") or "token exchange failed")
            raise GitHubOAuthError("GitHub OAuth token exchange failed: " + message)
        return data

    def _github_request(self, url: str, *, headers: dict[str, str]) -> Any:
        try:
            response = self.gateway.request_with_metadata(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    **headers,
                },
            )
        except InternetPolicyError as exc:
            raise GitHubOAuthError(str(exc)) from exc
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubOAuthError("GitHub identity response was not valid JSON") from exc


def _chmod_private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        raise GitHubOAuthError(f"unable to restrict permissions on {path}") from exc


__all__ = ["GitHubOAuthError", "GitHubOAuthService"]
