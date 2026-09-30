import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aurora.orchestration import daemon
from aurora.production_authorization import REQUIRED_PREREQUISITES


COMMIT = "d43db17e99803810fd951dd624d56978d0155f8d"


class _FakeRuntime:
    def __init__(self) -> None:
        self.once_called = False
        self.forever_called = False

    def submit_mission(self, *, objective: str) -> None:
        self.objective = objective

    def run_once(self):
        self.once_called = True

    def run_forever(self, stop):
        self.forever_called = True


def _write_record(path: Path, *, scope=None, expires=None):
    now = datetime.now(timezone.utc)
    payload = {
        "authorization_id": "auth-daemon-001",
        "approved_by": "human-operator",
        "issued_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (expires or now + timedelta(hours=1)).isoformat(),
        "scope": scope or ["director_live_execution"],
        "prerequisites": REQUIRED_PREREQUISITES,
        "live_enabled": True,
        "revoked": False,
        "kill_switch": False,
        "canonical_commit": COMMIT,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_daemon_normal_mode_does_not_require_live_authorization(monkeypatch):
    fake = _FakeRuntime()
    monkeypatch.setattr(daemon, "build_director_runtime", lambda **kwargs: fake)
    monkeypatch.setenv("AURORA_EGRESS_PROXY_HOST", "10.0.2.2")
    monkeypatch.delenv("AURORA_LIVE_AUTHORIZATION_FILE", raising=False)
    monkeypatch.delenv("AURORA_CANONICAL_COMMIT", raising=False)
    monkeypatch.setattr(sys, "argv", ["aurora", "--once"])

    assert daemon.main() == 0
    assert fake.once_called is True


def test_daemon_live_mode_fails_closed_before_building_runtime(monkeypatch, tmp_path: Path):
    def should_not_build(**kwargs):
        raise AssertionError("runtime must not be built when live authorization is denied")

    monkeypatch.setattr(daemon, "build_director_runtime", should_not_build)
    monkeypatch.setenv("AURORA_EGRESS_PROXY_HOST", "10.0.2.2")
    monkeypatch.setenv("AURORA_CANONICAL_COMMIT", COMMIT)
    monkeypatch.setenv(
        "AURORA_LIVE_AUTHORIZATION_FILE",
        str(tmp_path / "missing-live-authorization.json"),
    )
    monkeypatch.setattr(sys, "argv", ["aurora", "--live", "--once"])

    with pytest.raises(SystemExit):
        daemon.main()


def test_daemon_live_mode_requires_canonical_commit(monkeypatch, tmp_path: Path):
    auth_path = tmp_path / "live-authorization.json"
    _write_record(auth_path)

    def should_not_build(**kwargs):
        raise AssertionError("runtime must not be built without a canonical commit")

    monkeypatch.setattr(daemon, "build_director_runtime", should_not_build)
    monkeypatch.setenv("AURORA_EGRESS_PROXY_HOST", "10.0.2.2")
    monkeypatch.delenv("AURORA_CANONICAL_COMMIT", raising=False)
    monkeypatch.setenv("AURORA_LIVE_AUTHORIZATION_FILE", str(auth_path))
    monkeypatch.setattr(sys, "argv", ["aurora", "--live", "--once"])

    with pytest.raises(SystemExit):
        daemon.main()


def test_daemon_authorized_live_mode_reaches_existing_runtime_entrypoint(monkeypatch, tmp_path: Path):
    auth_path = tmp_path / "live-authorization.json"
    _write_record(auth_path)
    fake = _FakeRuntime()

    monkeypatch.setattr(daemon, "build_director_runtime", lambda **kwargs: fake)
    monkeypatch.setenv("AURORA_EGRESS_PROXY_HOST", "10.0.2.2")
    monkeypatch.setenv("AURORA_CANONICAL_COMMIT", COMMIT)
    monkeypatch.setenv("AURORA_LIVE_AUTHORIZATION_FILE", str(auth_path))
    monkeypatch.setattr(sys, "argv", ["aurora", "--live", "--once"])

    assert daemon.main() == 0
    assert fake.once_called is True


def test_daemon_live_scope_is_operation_specific(monkeypatch, tmp_path: Path):
    auth_path = tmp_path / "live-authorization.json"
    _write_record(auth_path, scope=["live_external_research"])

    def should_not_build(**kwargs):
        raise AssertionError("runtime must not be built outside authorization scope")

    monkeypatch.setattr(daemon, "build_director_runtime", should_not_build)
    monkeypatch.setenv("AURORA_EGRESS_PROXY_HOST", "10.0.2.2")
    monkeypatch.setenv("AURORA_CANONICAL_COMMIT", COMMIT)
    monkeypatch.setenv("AURORA_LIVE_AUTHORIZATION_FILE", str(auth_path))
    monkeypatch.setattr(
        sys,
        "argv",
        ["aurora", "--live", "--live-operation", "director_live_execution", "--once"],
    )

    with pytest.raises(SystemExit):
        daemon.main()
