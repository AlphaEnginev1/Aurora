from pathlib import Path
import json

from aurora.autonomy.internet import InternetResponse
from aurora.operator_settings import OperatorSettingsStore
from aurora.workbench.github_auth import GitHubOAuthService


class Gateway:
    def __init__(self):
        self.calls = []

    def request_with_metadata(self, url, *, method="GET", headers=None, body=None):
        self.calls.append((url, method, headers or {}, body))
        if url.endswith("/login/oauth/access_token"):
            return InternetResponse(
                requested_url=url,
                final_url=url,
                status_code=200,
                content_type="application/json",
                body=json.dumps({
                    "access_token": "gho-test-token",
                    "scope": "repo,workflow,read:user",
                    "token_type": "bearer",
                }).encode(),
            )
        return InternetResponse(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="application/json",
            body=json.dumps({
                "login": "aurora-user",
                "name": "Aurora User",
                "avatar_url": "https://avatars.githubusercontent.com/u/1",
            }).encode(),
        )


def test_github_oauth_round_trip_persists_identity_and_token(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AURORA_GITHUB_OAUTH_CLIENT_ID", "client")
    monkeypatch.setenv("AURORA_GITHUB_OAUTH_CLIENT_SECRET", "secret")
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    gateway = Gateway()
    service = GitHubOAuthService(store, gateway, runtime_root=tmp_path)

    url = service.authorization_url()
    state = json.loads((tmp_path / "github-oauth-state.json").read_text())["state"]
    assert "client_id=client" in url
    assert "code_challenge=" in url

    result = service.callback("temporary-code", state)

    assert result["connected"] is True
    assert result["login"] == "aurora-user"
    assert store.credential("github") == "gho-test-token"
    assert "gho-test-token" not in str(store.public())
    assert not (tmp_path / "github-oauth-state.json").exists()


def test_github_oauth_rejects_wrong_state(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AURORA_GITHUB_OAUTH_CLIENT_ID", "client")
    monkeypatch.setenv("AURORA_GITHUB_OAUTH_CLIENT_SECRET", "secret")
    store = OperatorSettingsStore(tmp_path / "operator-settings.json")
    service = GitHubOAuthService(store, Gateway(), runtime_root=tmp_path)
    service.authorization_url()

    try:
        service.callback("code", "wrong-state")
    except Exception as exc:
        assert "state validation failed" in str(exc)
    else:
        raise AssertionError("invalid OAuth state must fail closed")
