# Aurora external alerting and monitoring

## Alert channels

The daemon and independent monitor use `aurora.alerts.ExternalAlertSink`. All remote alert traffic must pass through Aurora's HTTPS CONNECT egress proxy. The proxy reads `allowed_hosts` and `allowed_ports` from `config/network-policy.json`; the current policy adds Telegram Bot API on 443 and SMTP implicit TLS hosts on 465.

Choose one channel using environment variables:

- Telegram: `AURORA_ALERT_CHANNEL=telegram`, `AURORA_ALERT_TELEGRAM_BOT_TOKEN`, `AURORA_ALERT_TELEGRAM_CHAT_ID`.
- SMTP over implicit TLS: `AURORA_ALERT_CHANNEL=smtp`, `AURORA_ALERT_SMTP_HOST=smtp.gmail.com` (or another explicitly allowlisted host), `AURORA_ALERT_SMTP_PORT=465`, `AURORA_ALERT_SMTP_USERNAME`, `AURORA_ALERT_SMTP_PASSWORD`, `AURORA_ALERT_SMTP_FROM`, `AURORA_ALERT_SMTP_TO`.
- Disabled: `AURORA_ALERT_CHANNEL=disabled` (default; no remote notifications).

Also configure `AURORA_EGRESS_PROXY_HOST`, `AURORA_EGRESS_PROXY_PORT` (default 3128), and optionally `AURORA_NETWORK_POLICY`. Custom SMTP hosts must be added to `allowed_hosts` in the network policy before use. Credentials must be supplied through the process environment or a protected environment file, never committed to the repository.

Runtime failure threshold/cooldown are configured with `AURORA_FAILURE_ALERT_THRESHOLD` (default 3) and `AURORA_FAILURE_ALERT_COOLDOWN_SECONDS` (default 900). Cross-process monitor deduplication uses `AURORA_ALERT_COOLDOWN_SECONDS` (default 900).

## Independent monitor

Run a one-shot check with:

```sh
AURORA_DASHBOARD_API_TOKEN='...' .venv/bin/python -m aurora.alert_monitor --once
```

The monitor calls `/api/dashboard/health` once, sends a `Bearer` token only when `AURORA_DASHBOARD_API_TOKEN` is configured, checks disk use, checks the configured proxy TCP endpoint, and evaluates expired task leases and recent HTTP 403/429 records. `AURORA_ALERT_STUCK_MINUTES` defaults to 10; `AURORA_ALERT_DISK_PERCENT` defaults to 80; `AURORA_ALERT_HTTP_ERROR_THRESHOLD` defaults to 5 events in the last 10 minutes. The state file makes cooldowns and stuck-condition duration survive one-shot process exits.

The supplied systemd units are deployment templates for an installation at `/opt/aurora` with service user `aurora`. Adjust paths/user if your installation differs, install both files in `/etc/systemd/system`, and put the dashboard token, proxy host/port, alert channel and credentials in root-readable `/etc/aurora/aurora.env` (for example mode 0640, owned by root and the Aurora group). Then run:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now aurora-alert-monitor.timer
systemctl list-timers aurora-alert-monitor.timer
journalctl -u aurora-alert-monitor.service
```

The monitor is independent of the Aurora daemon process and runs every minute, so service-down detection plus a successful channel delivery should occur within three minutes under normal network conditions. If the egress proxy itself is down, the monitor detects and logs that condition locally, but remote notification through that same unavailable proxy is impossible by design; recovery of the proxy is required before remote delivery can succeed.

## Alert types

- `failure_threshold` / `recovered`: emitted by the existing FailureTracker.
- `service_down`: health endpoint unavailable or invalid.
- `no_models_available`: health read model reports no usable model.
- `stuck_missions`: expired running-task leases remain present for the configured duration.
- `disk_high`: disk usage meets/exceeds the configured threshold.
- `proxy_unreachable`: configured egress proxy TCP endpoint is unavailable.
- `http_403_429_rate`: recent failure journal contains at least the configured number of 403/429 errors.

Alerts are deduplicated by condition/type and cooldown across timer invocations. The monitor does not restart or mutate Aurora; it reports state only.
