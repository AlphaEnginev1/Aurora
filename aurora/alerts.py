"""External alert delivery over Aurora's policy-enforced HTTPS CONNECT proxy.

Supported channels: Telegram Bot API and SMTP over implicit TLS (port 465).
No channel sends directly to the public network.
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import socket
import ssl
import threading
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Mapping
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

logger = logging.getLogger("aurora.alerts")


class AlertConfigurationError(RuntimeError):
    """Raised when an alert channel cannot be configured within network policy."""


def _policy_hosts(path: str | None = None) -> set[str]:
    policy_path = Path(path or os.getenv("AURORA_NETWORK_POLICY", "config/network-policy.json"))
    data = json.loads(policy_path.read_text(encoding="utf-8"))
    return {str(host).strip().lower() for host in data.get("allowed_hosts", []) if str(host).strip()}


def _proxy_url() -> str:
    host = os.getenv("AURORA_EGRESS_PROXY_HOST", "").strip()
    port = os.getenv("AURORA_EGRESS_PROXY_PORT", "3128").strip()
    if not host:
        raise AlertConfigurationError("AURORA_EGRESS_PROXY_HOST is required; direct egress is forbidden")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise AlertConfigurationError("AURORA_EGRESS_PROXY_PORT is invalid")
    return f"http://{host}:{int(port)}"


class _Cooldown:
    def __init__(self, seconds: float) -> None:
        self.seconds = max(0.0, seconds)
        self.lock = threading.Lock()
        self.last: dict[str, float] = {}

    def admit(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            previous = self.last.get(key)
            if previous is not None and now - previous < self.seconds:
                return False
            self.last[key] = now
            return True


class ExternalAlertSink:
    """Send redacted alert payloads to a configured channel via the egress proxy."""

    def __init__(self, *, channel: str | None = None, cooldown_seconds: float | None = None,
                 network_policy_path: str | None = None) -> None:
        self.channel = (channel or os.getenv("AURORA_ALERT_CHANNEL", "disabled")).strip().lower()
        self.cooldown = _Cooldown(float(cooldown_seconds if cooldown_seconds is not None else
                                        os.getenv("AURORA_ALERT_COOLDOWN_SECONDS", "900")))
        self.proxy_url = _proxy_url()
        self.allowed_hosts = _policy_hosts(network_policy_path)
        if self.channel not in {"disabled", "telegram", "smtp"}:
            raise AlertConfigurationError("AURORA_ALERT_CHANNEL must be disabled, telegram, or smtp")
        if self.channel == "telegram":
            self._require_host("api.telegram.org")
            if not os.getenv("AURORA_ALERT_TELEGRAM_BOT_TOKEN") or not os.getenv("AURORA_ALERT_TELEGRAM_CHAT_ID"):
                raise AlertConfigurationError("Telegram requires bot token and chat id environment variables")
        if self.channel == "smtp":
            host = os.getenv("AURORA_ALERT_SMTP_HOST", "smtp.gmail.com").strip().lower()
            if host not in self.allowed_hosts:
                raise AlertConfigurationError("SMTP host is not allowlisted in config/network-policy.json")
            if not os.getenv("AURORA_ALERT_SMTP_USERNAME") or not os.getenv("AURORA_ALERT_SMTP_PASSWORD"):
                raise AlertConfigurationError("SMTP requires username and password environment variables")
            if not os.getenv("AURORA_ALERT_SMTP_FROM") or not os.getenv("AURORA_ALERT_SMTP_TO"):
                raise AlertConfigurationError("SMTP requires from and to addresses")
            if "465" not in os.getenv("AURORA_ALERT_SMTP_PORT", "465"):
                raise AlertConfigurationError("SMTP alerts must use implicit TLS on proxy-allowed port 465")

    def _require_host(self, host: str) -> None:
        if host.lower() not in self.allowed_hosts:
            raise AlertConfigurationError(f"alert host {host} is not allowlisted in network policy")

    def __call__(self, payload: Mapping[str, Any]) -> None:
        if self.channel == "disabled":
            return
        kind = str(payload.get("kind", "unknown"))[:80]
        component = str(payload.get("component", "runtime"))[:120]
        # Deduplicate by alert kind + component; recovered is a distinct transition.
        if not self.cooldown.admit(f"{kind}:{component}"):
            logger.info("alert suppressed by cooldown (%s)", kind)
            return
        safe_payload = {
            "kind": kind,
            "component": component,
            "consecutive_failures": payload.get("consecutive_failures"),
            "total_failures": payload.get("total_failures"),
            "previous_consecutive_failures": payload.get("previous_consecutive_failures"),
            "message": str(payload.get("message", ""))[:500],
        }
        if self.channel == "telegram":
            self._send_telegram(safe_payload)
        elif self.channel == "smtp":
            self._send_smtp(safe_payload)

    @staticmethod
    def _render(payload: Mapping[str, Any]) -> str:
        lines = [f"Aurora alert: {payload['kind']}", f"Component: {payload['component']}"]
        for key in ("consecutive_failures", "total_failures", "previous_consecutive_failures"):
            if payload.get(key) is not None:
                lines.append(f"{key}: {payload[key]}")
        if payload.get("message"):
            lines.append(f"Details: {payload['message']}")
        return "\n".join(lines)

    def _send_telegram(self, payload: Mapping[str, Any]) -> None:
        import urllib.parse
        token = os.environ["AURORA_ALERT_TELEGRAM_BOT_TOKEN"]
        chat_id = os.environ["AURORA_ALERT_TELEGRAM_CHAT_ID"]
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        body = urllib.parse.urlencode({"chat_id": chat_id, "text": self._render(payload)}).encode()
        opener = build_opener(ProxyHandler({"https": self.proxy_url}))
        request = Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        try:
            with opener.open(request, timeout=10) as response:
                if response.status < 200 or response.status >= 300:
                    raise RuntimeError(f"Telegram returned HTTP {response.status}")
        except (OSError, URLError, TimeoutError) as exc:
            raise RuntimeError(f"Telegram alert delivery failed ({type(exc).__name__})") from exc

    def _send_smtp(self, payload: Mapping[str, Any]) -> None:
        host = os.getenv("AURORA_ALERT_SMTP_HOST", "smtp.gmail.com").strip().lower()
        port = int(os.getenv("AURORA_ALERT_SMTP_PORT", "465"))
        proxy_host = os.getenv("AURORA_EGRESS_PROXY_HOST", "").strip()
        proxy_port = int(os.getenv("AURORA_EGRESS_PROXY_PORT", "3128"))
        message = EmailMessage()
        message["Subject"] = f"[Aurora] {str(payload['kind'])[:80]}"
        message["From"] = os.environ["AURORA_ALERT_SMTP_FROM"]
        message["To"] = os.environ["AURORA_ALERT_SMTP_TO"]
        message.set_content(self._render(payload))

        class ProxiedSMTPSSL(smtplib.SMTP_SSL):
            def _get_socket(self, _host, _port, timeout):
                raw = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
                raw.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode("ascii"))
                response = b""
                while b"\r\n\r\n" not in response and len(response) < 8192:
                    chunk = raw.recv(1024)
                    if not chunk:
                        break
                    response += chunk
                if not response.startswith(b"HTTP/1.1 200") and not response.startswith(b"HTTP/1.0 200"):
                    raw.close()
                    raise OSError("egress proxy rejected SMTP CONNECT")
                return self.context.wrap_socket(raw, server_hostname=host)

        try:
            with ProxiedSMTPSSL(timeout=10, context=ssl.create_default_context()) as client:
                client.connect(host, port)
                client.login(os.environ["AURORA_ALERT_SMTP_USERNAME"], os.environ["AURORA_ALERT_SMTP_PASSWORD"])
                client.send_message(message)
        except Exception as exc:
            logger.error("SMTP alert delivery failed (%s)", type(exc).__name__)
            raise RuntimeError(f"SMTP alert delivery failed ({type(exc).__name__})") from exc


def build_alert_sink() -> ExternalAlertSink:
    """Construct the configured sink; disabled mode remains a safe no-op."""
    return ExternalAlertSink()
