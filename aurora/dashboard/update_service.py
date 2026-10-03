"""Safe local Aurora self-update orchestration."""
from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any


class DashboardUpdateError(RuntimeError):
    """Raised when a local Aurora update cannot be requested safely."""


class DashboardUpdateService:
    """Start the repository's guarded local updater as a detached process."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()

    @property
    def script_path(self) -> Path:
        return self.workspace_root / "scripts" / "update-local.sh"

    @property
    def status_path(self) -> Path:
        return self.workspace_root / ".aurora-runtime" / "update-status"

    def request_update(self) -> dict[str, Any]:
        script = self.script_path
        if not script.is_file():
            raise DashboardUpdateError(f"update script does not exist: {script}")

        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        log_path = self.status_path.parent / "update-request.log"
        with log_path.open("a", encoding="utf-8") as log:
            try:
                subprocess.Popen(
                    ["bash", str(script)],
                    cwd=self.workspace_root,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
            except (OSError, ValueError) as exc:
                raise DashboardUpdateError(f"unable to start Aurora updater: {exc}") from exc

        return {"ok": True, "state": "starting"}

    def status(self) -> dict[str, str]:
        if not self.status_path.is_file():
            return {"state": "unknown", "commit": "", "message": "No update has been requested"}

        values: dict[str, str] = {}
        for line in self.status_path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key in {"state", "commit", "message"}:
                values[key] = value
        return {
            "state": values.get("state", "unknown"),
            "commit": values.get("commit", ""),
            "message": values.get("message", ""),
        }


__all__ = ["DashboardUpdateError", "DashboardUpdateService"]
