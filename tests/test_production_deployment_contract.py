from pathlib import Path


ROOT = Path(__file__).parents[1]
DEPLOY = ROOT / "deploy"


def test_production_service_is_persistent_but_not_live_by_default():
    service = (DEPLOY / "aurora.service").read_text(encoding="utf-8")
    assert "--dashboard" in service
    assert "--live" not in service
    assert "Restart=always" in service
    assert "NoNewPrivileges=true" in service
    assert "EnvironmentFile=/etc/aurora/aurora.env" in service
    assert "ExecStartPre=/opt/aurora/deploy/aurora-preflight.sh" in service


def test_environment_template_has_no_real_credentials_and_selects_gemini_director():
    env = (DEPLOY / "aurora.env.example").read_text(encoding="utf-8")
    assert "AURORA_DIRECTOR_PROVIDER=gemini" in env
    assert "GEMINI_API_KEY=" in env
    assert "NVIDIA_API_KEY=" in env
    assert "AURORA_EGRESS_PROXY_HOST=" in env
    assert "AURORA_CANONICAL_COMMIT=" in env
    assert "BEGIN" not in env
    assert "Bearer " not in env


def test_preflight_is_fail_closed_for_runtime_prerequisites():
    script = (DEPLOY / "aurora-preflight.sh").read_text(encoding="utf-8")
    for marker in (
        "AURORA_EGRESS_PROXY_HOST",
        "AURORA_EGRESS_PROXY_PORT",
        "AURORA_CANONICAL_COMMIT",
        "NVIDIA_API_KEY",
        "AURORA_DIRECTOR_PROVIDER",
        "GEMINI_API_KEY",
        "aurora-central-avatar.jpg",
        "socket.create_connection",
    ):
        assert marker in script
