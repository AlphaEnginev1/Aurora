#!/usr/bin/env python3
"""Minimal, token-protected Windows companion for Aurora local computer work."""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = os.getenv("AURORA_LOCAL_GATEWAY_BIND", "127.0.0.1")
PORT = int(os.getenv("AURORA_LOCAL_GATEWAY_PORT", "8790"))
TOKEN = os.getenv("AURORA_LOCAL_GATEWAY_TOKEN", "").strip()
ALLOW_GUI = os.getenv("AURORA_LOCAL_ALLOW_GUI", "0").strip().lower() in {"1", "true", "yes"}
AUDIT_PATH = Path(os.getenv("AURORA_LOCAL_GATEWAY_AUDIT", str(Path.home() / ".aurora-local-gateway-audit.jsonl"))).expanduser()
MAX_STEPS = 40
MAX_BODY = 2 * 1024 * 1024
ROOTS = tuple(Path(item).expanduser().resolve() for item in os.getenv("AURORA_LOCAL_ROOTS", "").split(os.pathsep) if item.strip())


def _authorized(handler: BaseHTTPRequestHandler) -> bool:
    if not TOKEN:
        return False
    return handler.headers.get("Authorization", "") == f"Bearer {TOKEN}"


def _safe_path(raw: str) -> Path:
    candidate = Path(raw).expanduser().resolve()
    if not ROOTS or not any(candidate == root or root in candidate.parents for root in ROOTS):
        raise ValueError("path is outside AURORA_LOCAL_ROOTS")
    return candidate


def _audit(mission_id: str, actor: str, action: str, result: str) -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {"mission_id": mission_id, "actor": actor, "action": action, "result": result}
    with AUDIT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    server_version = "AuroraLocalGateway/1.0"

    def _check(self) -> bool:
        if not _authorized(self):
            _json(self, 401, {"ok": False, "error": "unauthorized"})
            return False
        return True

    def do_GET(self) -> None:
        if self.path != "/health" or not self._check():
            return
        _json(self, 200, {"ok": True, "ready": True, "roots": [str(root) for root in ROOTS]})

    def do_POST(self) -> None:
        if self.path != "/actions" or not self._check():
            if self.path != "/actions":
                _json(self, 404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                raise ValueError("request body too large or empty")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            actions = payload.get("actions")
            if not isinstance(actions, list) or len(actions) == 0 or len(actions) > MAX_STEPS:
                raise ValueError("invalid action list")
            mission_id = str(payload.get("mission_id", "")).strip()
            actor = str(payload.get("actor", "")).strip()
            if not mission_id or not actor:
                raise ValueError("mission_id and actor are required")
            results = []
            for action in actions:
                try:
                    result = self._run(action)
                    _audit(mission_id, actor, str(action.get("action", "unknown")), "succeeded")
                    results.append(result)
                except Exception:
                    _audit(mission_id, actor, str(action.get("action", "unknown")), "failed")
                    raise
            _json(self, 200, {"ok": True, "results": results})
        except Exception as exc:
            _json(self, 400, {"ok": False, "error": str(exc)[:1000]})

    def _run(self, action: dict) -> dict:
        if not isinstance(action, dict):
            raise ValueError("action must be an object")
        kind = str(action.get("action", "")).strip().lower()
        if kind == "list":
            path = _safe_path(str(action.get("path", "")))
            if not path.is_dir():
                raise ValueError("path is not a directory")
            return {"action": kind, "entries": [{"name": p.name, "kind": "directory" if p.is_dir() else "file"} for p in sorted(path.iterdir(), key=lambda p: p.name.lower())]}
        if kind == "read":
            path = _safe_path(str(action.get("path", "")))
            data = path.read_bytes()
            if len(data) > 1_048_576:
                raise ValueError("file exceeds 1 MiB limit")
            try:
                return {"action": kind, "path": str(path), "text": data.decode("utf-8")}
            except UnicodeDecodeError:
                return {"action": kind, "path": str(path), "base64": base64.b64encode(data).decode("ascii")}
        if kind == "write":
            path = _safe_path(str(action.get("path", "")))
            path.parent.mkdir(parents=True, exist_ok=True)
            content = action.get("content")
            if not isinstance(content, str) or len(content.encode("utf-8")) > 1_048_576:
                raise ValueError("write content invalid or too large")
            path.write_text(content, encoding="utf-8")
            return {"action": kind, "path": str(path), "bytes": path.stat().st_size}
        if kind == "open":
            path = _safe_path(str(action.get("path", "")))
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)], close_fds=True)
            return {"action": kind, "path": str(path), "opened": True}
        if kind == "screenshot":
            try:
                import pyautogui
            except ImportError as exc:
                raise ValueError("pyautogui is required for screenshot") from exc
            image = pyautogui.screenshot()
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return {"action": kind, "mime_type": "image/png", "data": base64.b64encode(buffer.getvalue()).decode("ascii")}
        if kind in {"click", "type", "hotkey"} and not ALLOW_GUI:
            raise ValueError("GUI mutation actions are disabled; set AURORA_LOCAL_ALLOW_GUI=1")
        if kind == "click":
            try:
                import pyautogui
            except ImportError as exc:
                raise ValueError("pyautogui is required for click") from exc
            pyautogui.click(int(action["x"]), int(action["y"]))
            return {"action": kind, "x": int(action["x"]), "y": int(action["y"])}
        if kind == "type":
            try:
                import pyautogui
            except ImportError as exc:
                raise ValueError("pyautogui is required for type") from exc
            text = action.get("text")
            if not isinstance(text, str) or len(text) > 10_000:
                raise ValueError("type text invalid or too large")
            pyautogui.write(text, interval=float(action.get("interval", 0.0)))
            return {"action": kind, "characters": len(text)}
        if kind == "hotkey":
            try:
                import pyautogui
            except ImportError as exc:
                raise ValueError("pyautogui is required for hotkey") from exc
            keys = action.get("keys")
            if not isinstance(keys, list) or not keys or len(keys) > 6 or not all(isinstance(key, str) for key in keys):
                raise ValueError("hotkey keys invalid")
            pyautogui.hotkey(*keys)
            return {"action": kind, "keys": list(keys)}
        raise ValueError(f"unsupported local action: {kind}")


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("AURORA_LOCAL_GATEWAY_TOKEN is required")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
