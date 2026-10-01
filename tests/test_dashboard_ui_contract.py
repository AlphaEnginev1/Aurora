from pathlib import Path


UI_DIR = Path(__file__).parents[1] / "aurora" / "dashboard" / "ui"


def test_dashboard_ui_assets_are_present():
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    css = (UI_DIR / "styles.css").read_text(encoding="utf-8")
    js = (UI_DIR / "app.js").read_text(encoding="utf-8")

    assert 'href="./styles.css"' in html
    assert '<script src="./app.js" defer></script>' in html
    assert 'id="director-portrait"' in html
    assert 'id="agent-orbit"' in html
    assert 'id="projects-list"' in html
    assert 'id="mailbox-list"' in html
    assert 'id="model-list"' in html
    assert 'id="skills-list"' in html
    assert 'id="runtime-summary"' in html
    assert ".portrait-stage" in css
    assert ".agent-orbit" in css


def test_dashboard_ui_uses_gateway_endpoints_only():
    js = (UI_DIR / "app.js").read_text(encoding="utf-8")

    assert '"/snapshot"' in js
    assert '"/projects/"' in js
    assert '"/agents/"' in js
    assert "AURORA_DASHBOARD_API_BASE" in js
    assert "sqlite" not in js.lower()
    assert "task_events" not in js
    assert "mission_messages" not in js
    assert "provider.generate" not in js


def test_dashboard_ui_uses_resilient_live_sync_and_defers_avatar():
    js = (UI_DIR / "app.js").read_text(encoding="utf-8")
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")

    assert "scheduleRefresh" in js
    assert "visibilitychange" in js
    assert "retryDelay" in js
    assert "invalid snapshot contract" in js
    assert "portrait-stage" in html
