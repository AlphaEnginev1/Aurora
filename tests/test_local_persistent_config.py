import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
LOADER = ROOT / "scripts" / "local-env.sh"
CONFIGURATOR = ROOT / "scripts" / "configure-local-openrouter.sh"
LAUNCHER = ROOT / "scripts" / "launch-local.sh"


def run_loader(config_file: Path, *, expected_key: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AURORA_LOCAL_ENV_FILE"] = str(config_file)
    if expected_key is not None:
        env["EXPECTED_TEST_KEY"] = expected_key
    command = r'''
set -e
source "$1"
aurora_load_local_env
if [[ -n "${EXPECTED_TEST_KEY:-}" ]]; then
  [[ "${OPENROUTER_API_KEY:-}" == "$EXPECTED_TEST_KEY" ]]
fi
printf '%s\n%s\n' "${AURORA_DIRECTOR_PROVIDER:-router}" "${OPENROUTER_MODEL_ID:-}"
'''
    return subprocess.run(
        ["bash", "-c", command, "bash", str(LOADER)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_configurator_persists_hidden_key_with_restricted_permissions(tmp_path: Path) -> None:
    config_home = tmp_path / "xdg-config"
    test_key = "sk-or-v1-unit-test-not-a-real-secret"
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(config_home)
    env["OPENROUTER_API_KEY"] = test_key

    result = subprocess.run(
        ["bash", str(CONFIGURATOR)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    config_dir = config_home / "aurora"
    config_file = config_dir / "aurora.env"
    assert result.returncode == 0, result.stderr
    assert test_key not in result.stdout
    assert test_key not in result.stderr
    assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600
    assert config_file.read_text(encoding="utf-8") == (
        f"OPENROUTER_API_KEY={test_key}\n"
        "AURORA_DIRECTOR_PROVIDER=openrouter\n"
        "OPENROUTER_MODEL_ID=openrouter/free\n"
    )


def test_loader_restores_provider_and_model_without_executing_config(tmp_path: Path) -> None:
    config_file = tmp_path / "aurora.env"
    test_key = "sk-or-v1-test-with==signs"
    config_file.write_text(
        f"OPENROUTER_API_KEY={test_key}\n"
        "AURORA_DIRECTOR_PROVIDER=openrouter\n"
        "OPENROUTER_MODEL_ID=openrouter/free\n",
        encoding="utf-8",
    )
    config_file.chmod(0o644)

    result = run_loader(config_file, expected_key=test_key)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "openrouter\nopenrouter/free\n"
    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600


def test_loader_rejects_unapproved_values_without_evaluating_them(tmp_path: Path) -> None:
    config_file = tmp_path / "aurora.env"
    sentinel = tmp_path / "should-not-exist"
    config_file.write_text(
        f"AURORA_DIRECTOR_PROVIDER=$(touch {sentinel})\n",
        encoding="utf-8",
    )

    result = run_loader(config_file)

    assert result.returncode != 0
    assert not sentinel.exists()


def test_launcher_loads_persistent_environment_before_starting_runtime() -> None:
    script = LAUNCHER.read_text(encoding="utf-8")
    assert 'source "$ROOT/scripts/local-env.sh"' in script
    assert "aurora_load_local_env" in script
    assert script.index("aurora_load_local_env") < script.index('nohup env')


def test_restart_script_cleans_orphaned_aurora_daemons_before_starting() -> None:
    script = (ROOT / "scripts" / "restart-local.sh").read_text(encoding="utf-8")
    assert "find_module_pids()" in script
    assert 'args[1:3] != ["-m", module]' in script
    assert 'stop_module "aurora.orchestration.daemon" "--dashboard"' in script
    assert 'stop_module "aurora.autonomy.egress_proxy_daemon" ""' in script
    assert 'kill -TERM "$pid"' in script
    assert 'rm -f -- "$RUNTIME/aurora.pid" "$RUNTIME/egress-proxy.pid"' in script
    assert 'exec bash "$ROOT/scripts/launch-local.sh"' in script


def test_update_script_uses_verified_restart_helper() -> None:
    script = (ROOT / "scripts" / "update-local.sh").read_text(encoding="utf-8")

    restart_call = 'if ! bash "$ROOT/scripts/restart-local.sh"; then'
    ready_status = 'write_status "ready" "$updated" "Aurora updated and dashboard is ready"'

    assert restart_call in script
    assert 'nohup bash "$ROOT/scripts/launch-local.sh"' not in script
    assert "dashboard_process_matches()" in script
    assert "Aurora restart failed; inspect .aurora-runtime/update.log" in script
    assert script.index(restart_call) < script.index(ready_status)

