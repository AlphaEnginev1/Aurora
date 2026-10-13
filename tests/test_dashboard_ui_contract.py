from pathlib import Path
import shutil
import subprocess

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
    # Settings uses one provider-agnostic form; the legacy NVIDIA-only input is gone.
    assert 'id="credential-provider"' in html
    assert 'id="credential-endpoint"' in html
    assert 'id="credential-key"' in html
    assert 'id="credential-save"' in html
    assert 'id="credential-inventory"' in html
    assert 'id="credential-refresh"' in html
    assert 'id="credential-test-provider"' in html
    # Local settings use restrictive filesystem permissions; do not claim
    # at-rest encryption because operator-settings.json is not encrypted.
    assert "LOCAL CREDENTIAL STORE" in html
    assert "LOCAL ENCRYPTION BOUNDARY" not in html
    assert 'id="nvidia-key"' not in html
    provider_js = (UI_DIR / "provider_credentials.js").read_text(encoding="utf-8")
    assert '"/settings/test"' in provider_js
    assert "testSelectedProvider" in provider_js
    assert "data.provider_catalog" in provider_js
    assert "provider.provider_id" in provider_js
    assert '<script src="./provider_credentials.js" defer></script>' in html
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
    home = html[html.index('<section id="view-home"'):html.index('<section id="view-projects"')]
    assert home.index('id="director-portrait"') < home.index('class="hero-copy"') < home.index('class="panel chat-surface chat-workspace"')
    assert "Aurora Director-centered refresh" in css
    assert "@media (max-width: 760px)" in css
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


def test_home_chat_attachments_and_live_roadmap_office_contract():
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    css = (UI_DIR / "styles.css").read_text(encoding="utf-8")
    js = (UI_DIR / "app.js").read_text(encoding="utf-8")

    # The overview now lives on the left of a full-size centre chat.
    assert 'class="home-grid home-grid-redesigned"' in html
    assert 'class="home-sidebar"' in html
    assert 'id="chat-messages"' in html
    assert 'id="chat-composer"' not in html  # the composer is styled by class and has existing controls
    assert 'id="upload-aurora"' not in html
    assert 'id="attach-file"' in html
    assert html.count('id="aurora-file-input"') == 1
    assert 'id="chat-history-body"' in html
    assert 'background: #000 !important' in css
    assert ".chat-composer" in css
    assert ".chat-message" in css

    # Roadmap is a live view backed by per-agent endpoint data, not hard-coded statuses.
    assert '<section id="view-roadmap" class="view" data-view-panel="roadmap"' in html
    assert 'data-nav-target="roadmap"' in html
    assert html.index('data-nav-target="home"') < html.index('data-nav-target="roadmap"')
    assert 'id="roadmap-office-grid"' in html
    assert 'id="roadmap-inspector-body"' in html
    assert 'id="roadmap-mailbox-slot"' in html
    assert 'id="roadmap-house"' in html
    assert 'id="roadmap-room-research"' in html
    assert 'id="roadmap-room-engineering"' in html
    assert 'id="roadmap-director-models"' in html
    assert 'id="roadmap-model-count"' in html
    assert 'id="roadmap-candidate-count"' in html
    assert ".floorplan-model-station[data-model-state=\"candidate\"]" in css
    assert ".floorplan-model-station[data-model-state=\"working\"]" in css
    assert ".floorplan-central-corridor" in css
    assert "roadmapSelectedModelId" in js
    assert "roadmapSelectedStationKey" in js
    assert "data-roadmap-station-key" in js
    assert 'button.getAttribute("data-roadmap-station-key") === state.roadmapSelectedStationKey' in js
    assert "roadmapModelCatalog" in js
    assert "model_activity" in js
    assert "active_executions" in js
    assert "model_candidates" in js
    assert 'state.activeView === "roadmap"' in js
    assert "loadRoadmapDetails" in js
    assert 'state.apiBase + "/agents/"' in js
    assert "current_tasks" in js
    assert "roadmapSeenCompletionEvents" in js
    assert "triggerRoadmapDelivery" in js

    # Attachments must go through Aurora's governed intake endpoint.
    assert 'state.apiBase + "/artifacts"' in js
    assert "state.pendingAttachments" in js
    assert "mission_id" in js
    assert '$("attach-file").addEventListener' in js



def test_dashboard_javascript_syntax_when_node_is_available():
    node = shutil.which("node")
    if node is None:
        return
    for script in ("app.js", "provider_credentials.js"):
        result = subprocess.run(
            [node, "--check", str(UI_DIR / script)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{script}: {result.stderr}"

def test_dashboard_navigation_and_home_overview_swap_sides():
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    css = (UI_DIR / "styles.css").read_text(encoding="utf-8")

    shell_start = html.index('<div class="workspace-shell">')
    main_start = html.index('<main class="workspace">', shell_start)
    nav_start = html.index('<nav class="right-rail" aria-label="Aurora navigation">', shell_start)
    shell_end = html.index('</main>', main_start)
    assert nav_start < main_start < shell_end

    # The navigation occupies the narrow first desktop grid column.
    assert 'grid-template-columns: 78px minmax(0, 1fr)' in css
    # On the Home view, the chat stays in column one and the same overview
    # panels move to column two without changing their DOM or styling.
    assert '.home-grid.home-grid-redesigned > .home-sidebar {\n  grid-column: 2;\n  grid-row: 1;' in css
    assert '.home-grid.home-grid-redesigned > .chat-workspace {\n  grid-column: 1;\n  grid-row: 1;' in css
    # Keep the existing stacked layout on narrow screens.
    assert '@media (max-width: 980px)' in css
    assert 'grid-column: auto;\n    grid-row: auto;' in css

