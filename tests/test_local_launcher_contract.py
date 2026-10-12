from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_local_launcher_starts_proxy_and_dashboard():
    script = (ROOT / "scripts" / "launch-local.sh").read_text(encoding="utf-8")
    for marker in (
        "aurora.autonomy.egress_proxy_daemon",
        "aurora.orchestration.daemon",
        "AURORA_EGRESS_PROXY_HOST",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "AURORA_DASHBOARD_PORT=8787",
        "OPENROUTER_API_KEY",
        "AURORA_DIRECTOR_PROVIDER",
        "OPENROUTER_MODEL_ID",
        "scripts/local-env.sh",
        "aurora_load_local_env",
        ".aurora-runtime",
        "aurora.pid",
        "process_matches",
        "/proc/$pid/cmdline",
        "Aurora dashboard did not become ready from its new daemon process.",
        "PROXY_READY",
        "DASHBOARD_READY",
        'DAEMON_STARTED_PID="$!"',
    ):
        assert marker in script


def test_windows_shortcut_targets_wsl_ubuntu():
    script = (ROOT / "scripts" / "install-desktop-launcher.ps1").read_text(encoding="utf-8")
    assert "CreateShortcut" in script
    assert "Aurora.lnk" in script
    assert "-d Ubuntu" in script
    assert "--cd ~" in script
    assert "exec bash scripts/launch-local.sh" in script
    assert "launch-local.sh" in script


def test_launcher_does_not_accept_another_process_as_its_dashboard():
    script = (ROOT / "scripts" / "launch-local.sh").read_text(encoding="utf-8")
    assert 'process_matches "$DAEMON_STARTED_PID" "aurora.orchestration.daemon --dashboard"' in script
    assert 'curl -fsS --max-time 2 http://127.0.0.1:8787/' in script
    assert "sleep 1" in script
    assert script.index('process_matches "$DAEMON_STARTED_PID"') < script.index('echo "Aurora dashboard: http://127.0.0.1:8787"')
