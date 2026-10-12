from pathlib import Path

UI_DIR = Path(__file__).parents[1] / "aurora" / "dashboard" / "ui"


def test_dashboard_ui_assets_are_present():
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    css = (UI_DIR / "styles.css").read_text(encoding="utf-8")
    js = (UI_DIR / "app.js").read_text(encoding="utf-8")

    assert 'href="./styles.css"' in html
    assert '<script src="./app.js" defer></script>' in html
    assert 'id="director-portrait"' in html
    assert 'data-nav-target="home"' in html
    assert 'data-nav-target="projects"' in html
    assert 'data-nav-target="tasks"' in html
    assert 'data-nav-target="research"' in html
    assert 'data-nav-target="knowledge"' in html
    assert 'data-nav-target="models"' in html
    assert 'data-nav-target="system"' in html
    assert 'data-nav-target="health"' in html
    assert 'data-nav-target="settings"' in html
    assert 'id="director-provider"' in html
    assert 'id="gemini-key"' not in html
    assert 'id="nvidia-key"' in html
    assert 'id="chat-expand"' in html
    assert '<section id="view-health" class="view"' in html
    assert '\n        id="view-health"' not in html
    assert 'id="chat-history"' in html
    assert 'id="projects-board"' in html
    assert 'id="task-matrix"' in html
    assert 'id="events-list"' in html
    assert 'id="research-overview"' in html
    assert 'id="knowledge-index"' in html
    assert 'id="model-list"' in html
    assert 'id="skills-list"' in html
    assert 'id="runtime-summary"' in html
    assert ".portrait-stage" in css
    assert ".right-rail" in css
    assert ".history-drawer" in css
    assert "setView" in js
    assert "renderHealth" in js
    assert "Incident Detail" in js


def test_dashboard_ui_uses_gateway_endpoints_only():
    js = (UI_DIR / "app.js").read_text(encoding="utf-8")

    assert '"/snapshot"' in js
    assert '"/projects/"' in js
    assert '"/agents/"' in js
    assert '"/turns"' in js
    assert '"/settings"' in js
    assert '"/settings/test"' in js
    assert '"/health"' in js
    assert "api_key_present" in js
    assert "AURORA_DASHBOARD_API_BASE" in js
    assert "sqlite" not in js.lower()
    assert "task_events" not in js


def test_dashboard_bootstrap_initializers_are_defined():
    js = (UI_DIR / "app.js").read_text(encoding="utf-8")

    assert "function setupGitHubConnection()" in js
    assert "async function loadGitHubStatus()" in js
    assert "setupConversation();" in js
    assert "loadSnapshot();" in js
