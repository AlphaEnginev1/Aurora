from pathlib import Path

from aurora.dashboard.update_service import DashboardUpdateService


def test_update_service_reports_unknown_status(tmp_path: Path):
    service = DashboardUpdateService(tmp_path)

    status = service.status()

    assert status["state"] == "unknown"
    assert status["message"] == "No update has been requested"


def test_update_service_starts_detached_updater(tmp_path: Path, monkeypatch):
    script = tmp_path / "scripts" / "update-local.sh"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")

    calls = {}

    class FakePopen:
        def __init__(self, *args, **kwargs):
            calls["args"] = args
            calls["kwargs"] = kwargs

    monkeypatch.setattr(
        "aurora.dashboard.update_service.subprocess.Popen",
        FakePopen,
    )

    service = DashboardUpdateService(tmp_path)
    result = service.request_update()

    assert result == {"ok": True, "state": "starting"}
    assert calls["args"][0][0] == "bash"
    assert calls["args"][0][1] == str(script)
    assert calls["kwargs"]["cwd"] == tmp_path
    assert calls["kwargs"]["start_new_session"] is True
    assert calls["kwargs"]["close_fds"] is True


def test_update_service_reads_status_file(tmp_path: Path):
    runtime = tmp_path / ".aurora-runtime"
    runtime.mkdir()
    (runtime / "update-status").write_text(
        "state=ready\ncommit=abc123\nmessage=Aurora updated and dashboard is ready\n",
        encoding="utf-8",
    )

    status = DashboardUpdateService(tmp_path).status()

    assert status == {
        "state": "ready",
        "commit": "abc123",
        "message": "Aurora updated and dashboard is ready",
    }
