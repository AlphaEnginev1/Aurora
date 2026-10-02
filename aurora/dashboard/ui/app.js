(() => {
  "use strict";

  const state = {
    snapshot: null,
    avatarVoice: null,
    refreshTimer: null,
    retryDelay: 10000,
    inFlight: false,
    apiBase: window.AURORA_DASHBOARD_API_BASE || "/api/dashboard",
    portraitUrl: window.AURORA_AVATAR_PORTRAIT_URL || "./aurora-central-avatar.jpg",
    conversationApiBase: window.AURORA_CONVERSATION_API_BASE || "/api/conversation",
    conversationSessionId: null,
    conversationInFlight: false,
    activeView: "home",
    settings: null
  };

  const $ = (id) => document.getElementById(id);

  function esc(value) {
    return String(value == null ? "—" : value)
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  }

  function dateText(value) {
    if (!value) return "unknown time";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  }

  function safeArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function safeObject(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function setUnknown(reason) {
    $("system-status").textContent = "SYSTEM UNKNOWN";
    $("system-status").className = "status-pill unknown";
    $("freshness").textContent = reason;
  }

  function setView(view) {
    state.activeView = view;
    document.querySelectorAll("[data-view-panel]").forEach(function (panel) {
      panel.classList.toggle("active", panel.getAttribute("data-view-panel") === view);
    });
    if (view === "settings") loadSettings().catch(function (error) {
      showSettingsMessage("Settings unavailable: " + error.message, true);
    });
    document.querySelectorAll("[data-nav-target]").forEach(function (button) {
      if (button.classList.contains("rail-button")) {
        button.classList.toggle("active", button.getAttribute("data-nav-target") === view);
      }
    });
  }

  function setupNavigation() {
    document.querySelectorAll("[data-nav-target]").forEach(function (button) {
      button.addEventListener("click", function () {
        setView(button.getAttribute("data-nav-target"));
      });
    });
  }

  async function loadSnapshot({ quiet = false } = {}) {
    if (state.inFlight) return;
    state.inFlight = true;
    const button = $("refresh");
    if (!quiet) button.disabled = true;

    try {
      const response = await fetch(state.apiBase + "/snapshot", { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("dashboard API " + response.status);
      const next = await response.json();
      if (!next || typeof next !== "object" || !next.director) throw new Error("invalid snapshot contract");
      state.snapshot = next;
      state.retryDelay = 10000;
      render();
    } catch (error) {
      setUnknown("Live sync disconnected; retrying: " + error.message);
      state.retryDelay = Math.min(Math.max(state.retryDelay * 2, 10000), 120000);
    } finally {
      state.inFlight = false;
      if (!quiet) button.disabled = false;
      scheduleRefresh();
    }
  }

  function render() {
    const data = state.snapshot;
    if (!data) return;

    const readiness = data.director && data.director.readiness || "UNKNOWN";
    $("system-status").textContent = readiness === "READY" ? "SYSTEM READY" : "SYSTEM UNKNOWN";
    $("system-status").className = "status-pill " + (readiness === "READY" ? "ready" : "unknown");
    $("freshness").textContent = "Live · snapshot " + dateText(data.generated_at);

    $("director-name").textContent = data.director.identity || "Aurora Director";
    const unknown = safeArray(data.director.context_unknown_fields);
    $("director-readiness").textContent = unknown.length
      ? "Context has " + unknown.length + " unknown field(s)."
      : "Authoritative Director context loaded.";

    const configured = safeArray(data.director.configured_models);
    $("director-model").textContent = configured.length
      ? (configured[0].id || configured[0].model_id || "configured")
      : "unknown";
    $("director-context").textContent = data.director.context_manifest_id
      ? String(data.director.context_manifest_id).slice(0, 14)
      : "unknown";

    const projects = safeArray(data.projects);
    const agents = safeArray(data.organization && data.organization.agents);
    const runtime = safeObject(data.runtime);
    const counts = safeObject(runtime.task_status_counts);

    $("project-count").textContent = String(projects.length);
    $("active-project-count").textContent = String(projects.filter(function (p) { return p.status === "active"; }).length) + " active";
    $("pulse-projects").textContent = String(projects.length);
    $("pulse-tasks").textContent = String(runtime.task_count || 0);
    $("pulse-models").textContent = String(safeArray(data.models).length);
    $("pulse-research").textContent = safeObject(data.research).state || (data.research ? "READY" : "UNKNOWN");
    $("tasks-view-count").textContent = String(runtime.task_count || 0) + " tasks";

    renderProjects(projects, $("projects-list"), true);
    renderProjects(projects, $("projects-board"), false);
    renderTasks(counts, runtime);
    renderResearch(data.research);
    renderKnowledge(data.research, data.governance);
    renderModels(safeArray(data.models), safeArray(data.providers));
    renderSkills(safeArray(data.skills));
    renderRuntime(runtime);
    renderDecision(data.governance);
    renderMailboxEvents(runtime.latest_events);

    const eventRows = safeArray(runtime.latest_events);
    const newest = eventRows.reduce(function (max, event) {
      return Math.max(max, Number(event.event_id) || 0);
    }, 0);
    $("freshness").textContent = "Live · snapshot " + dateText(data.generated_at) + (newest ? " · event #" + newest : "");
  }

  function projectButton(project) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "project-tile";
    item.innerHTML =
      "<h3>" + esc(project.name || project.project_id) + "</h3>" +
      "<p>" + esc(project.objective || "No objective supplied.") + "</p>" +
      '<div class="project-state"><span>' + esc(project.project_id) + '</span><strong>' + esc(String(project.status || "unknown").toUpperCase()) + "</strong></div>";
    item.addEventListener("click", function () {
      openInspector("Project Inspector", project.name || project.project_id, fetchProject(project.project_id));
    });
    return item;
  }

  function renderProjects(projects, target, compact) {
    target.innerHTML = "";
    if (!projects.length) {
      target.innerHTML = '<div class="empty-state">No projects registered.</div>';
      return;
    }

    projects.slice(0, compact ? 6 : 50).forEach(function (project) {
      if (compact) {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "list-item";
        item.innerHTML =
          '<div class="list-item-head"><strong>' + esc(project.name || project.project_id) + "</strong>" +
          '<span class="badge">' + esc(project.status) + "</span></div>" +
          "<p>" + esc(project.objective || "No objective supplied.") + "</p>";
        item.addEventListener("click", function () {
          openInspector("Project Inspector", project.name || project.project_id, fetchProject(project.project_id));
        });
        target.appendChild(item);
      } else {
        target.appendChild(projectButton(project));
      }
    });
  }

  function renderTasks(counts, runtime) {
    const keys = ["pending", "running", "completed", "failed"];
    const labels = { pending: "PENDING", running: "RUNNING", completed: "COMPLETED", failed: "FAILED" };
    $("task-matrix").innerHTML = keys.map(function (key) {
      return '<div class="task-cell"><span>' + labels[key] + '</span><strong>' + esc(counts[key] || 0) + '</strong><em>authoritative runtime count</em></div>';
    }).join("");
  }

  function renderMailboxEvents(events) {
    const target = $("events-list");
    target.innerHTML = "";
    const rows = safeArray(events).slice(-18).reverse();
    if (!rows.length) {
      target.innerHTML = '<div class="empty-state">No runtime events.</div>';
      return;
    }
    rows.forEach(function (event) {
      const item = document.createElement("div");
      item.className = "timeline-item";
      item.innerHTML =
        "<strong>" + esc(event.kind || event.type || "EVENT") + "</strong>" +
        "<span>" + esc(event.content || event.message || JSON.stringify(event)) + " · " + esc(dateText(event.created_at || event.timestamp)) + "</span>";
      target.appendChild(item);
    });
  }

  function renderResearch(research) {
    research = safeObject(research);
    const stateText = research.state || "UNKNOWN";
    $("research-view-state").textContent = String(stateText).toUpperCase();

    const sourceCount = Array.isArray(research.sources) ? research.sources.length : (research.source_count || 0);
    const artifactCount = Array.isArray(research.artifacts) ? research.artifacts.length : (research.artifact_count || 0);
    const citationCount = Array.isArray(research.citations) ? research.citations.length : (research.citation_count || 0);
    const queryCount = research.query_count || research.queries || 0;

    $("research-overview").innerHTML =
      '<div class="research-card"><span>STATE</span><strong>' + esc(String(stateText).toUpperCase()) + '</strong><em>research store boundary</em></div>' +
      '<div class="research-card"><span>QUERIES</span><strong>' + esc(queryCount) + '</strong><em>recorded research activity</em></div>' +
      '<div class="research-card"><span>SOURCES</span><strong>' + esc(sourceCount) + '</strong><em>available source objects</em></div>' +
      '<div class="research-card"><span>ARTIFACTS</span><strong>' + esc(artifactCount) + '</strong><em>trusted knowledge objects</em></div>' +
      '<div class="research-card"><span>CITATIONS</span><strong>' + esc(citationCount) + '</strong><em>provenance relationships</em></div>';

    const target = $("knowledge-surface");
    target.innerHTML = "";
    const candidates = [
      ["STATE", research.state || "unknown"],
      ["REASON", research.reason || "research boundary is authoritative and read-only"],
      ["LAST CAPTURE", research.freshness && research.freshness.captured_at ? dateText(research.freshness.captured_at) : "—"]
    ];
    candidates.forEach(function (row) {
      const item = document.createElement("div");
      item.className = "list-item";
      item.innerHTML = "<strong>" + esc(row[0]) + "</strong><p>" + esc(row[1]) + "</p>";
      target.appendChild(item);
    });
  }

  function renderKnowledge(research, governance) {
    research = safeObject(research);
    governance = safeObject(governance);
    const index = $("knowledge-index");
    index.innerHTML = "";
    const knowledge = [
      ["RESEARCH STORE", research.state || "unknown", "Research read model"],
      ["DECISION PERFORMANCE", safeArray(governance.decision_performance).length, "Observed decision evaluations"],
      ["DECISION KNOWLEDGE", safeArray(governance.decision_knowledge).length, "Stored decision learning records"],
      ["ACTIVE POLICY", governance.active_policy ? "AVAILABLE" : "NONE", "Director policy surface"],
      ["CONTEXT", governance.context_manifest_id || "unknown", "Current context manifest"]
    ];
    knowledge.forEach(function (row) {
      const node = document.createElement("div");
      node.className = "knowledge-node";
      node.innerHTML = "<span>" + esc(row[0]) + "</span><strong>" + esc(row[1]) + "</strong><p>" + esc(row[2]) + "</p>";
      index.appendChild(node);
    });

    const target = $("governance-list");
    target.innerHTML = "";
    const decisions = safeArray(governance.decision_performance).slice(0, 10);
    if (!decisions.length) {
      target.innerHTML = '<div class="empty-state">No decision-performance rows available.</div>';
      return;
    }
    decisions.forEach(function (row) {
      const item = document.createElement("div");
      item.className = "list-item";
      item.innerHTML = "<strong>" + esc(row.decision_id || row.policy_id || "decision") + "</strong><p>" + esc(JSON.stringify(row)) + "</p>";
      target.appendChild(item);
    });
  }

  function renderModels(models, providers) {
    $("model-count").textContent = String(models.length) + " models";
    const target = $("model-list");
    target.innerHTML = "";
    if (!models.length) {
      target.innerHTML = '<div class="empty-state">No models registered.</div>';
    } else {
      models.slice(0, 50).forEach(function (model) {
        const item = document.createElement("div");
        item.className = "list-item";
        item.innerHTML =
          '<div class="list-item-head"><strong>' + esc(model.model_id || model.id) + "</strong>" +
          '<span class="badge">' + esc(model.provider) + "</span></div>" +
          "<p>" + esc(safeArray(model.capabilities).join(", ") || "no capabilities") + " · health " + esc(model.healthy) + "</p>";
        target.appendChild(item);
      });
    }

    const providerTarget = $("provider-list");
    providerTarget.innerHTML = "";
    if (!providers.length) {
      providerTarget.innerHTML = '<div class="empty-state">No providers registered.</div>';
      return;
    }
    providers.forEach(function (provider) {
      const item = document.createElement("div");
      item.className = "provider-card";
      item.innerHTML = "<strong>" + esc(provider.name || provider.provider_id || provider.id) + "</strong><span>" + esc(safeArray(provider.capabilities).join(" · ")) + "</span>";
      providerTarget.appendChild(item);
    });
  }

  function renderSkills(skills) {
    const target = $("skills-list");
    target.innerHTML = "";
    if (!skills.length) {
      target.innerHTML = '<div class="empty-state">No skills registered.</div>';
      return;
    }
    skills.slice(0, 20).forEach(function (skill) {
      const item = document.createElement("div");
      item.className = "list-item";
      item.innerHTML =
        '<div class="list-item-head"><strong>' + esc(skill.name || skill.skill_id) + '</strong><span class="badge">v' + esc(skill.version) + "</span></div>" +
        "<p>" + esc(skill.mission || "No mission supplied.") + "</p>";
      target.appendChild(item);
    });
  }

  function renderRuntime(runtime) {
    const counts = safeObject(runtime.task_status_counts);
    const target = $("runtime-summary");
    target.innerHTML =
      '<div class="runtime-card"><span>MISSIONS</span><strong>' + esc(runtime.mission_count || 0) + '</strong><em>stored missions</em></div>' +
      '<div class="runtime-card"><span>TASKS</span><strong>' + esc(runtime.task_count || 0) + '</strong><em>stored tasks</em></div>' +
      '<div class="runtime-card"><span>RUNNING</span><strong>' + esc(counts.running || 0) + '</strong><em>current state</em></div>' +
      '<div class="runtime-card"><span>FAILED</span><strong>' + esc(counts.failed || 0) + '</strong><em>current state</em></div>';
  }

  function renderDecision(governance) {
    const target = $("decision-summary");
    governance = safeObject(governance);
    const decisions = safeArray(governance.decision_performance);
    const latest = decisions.length ? decisions[0] : null;
    target.innerHTML =
      '<div class="decision-card"><span>DECISION PERFORMANCE</span><strong>' + esc(latest ? (latest.decision_id || latest.policy_id || "recorded") : "No evaluation rows") + '</strong><em>' + esc(latest ? "Evidence-backed learning surface" : "Awaiting authoritative evaluation data") + '</em></div>' +
      '<div class="decision-card"><span>ACTIVE POLICY</span><strong>' + esc(governance.active_policy ? "AVAILABLE" : "NOT CONFIGURED") + '</strong><em>governance boundary</em></div>';
  }

  function showSettingsMessage(message, error) {
    const target = $("settings-message");
    if (!target) return;
    target.textContent = message;
    target.className = "settings-message " + (error ? "error" : "success");
  }

  function renderSettings(data) {
    state.settings = data;
    const director = safeObject(data.director);
    const providers = safeObject(data.providers);
    $("director-provider").value = String(director.provider || "gemini");
    $("director-model").value = String(director.model || "");
    $("director-endpoint").value = String(director.endpoint || "");
    $("nvidia-endpoint").value = String(
      safeObject(providers.nvidia_api).base_url || "https://integrate.api.nvidia.com/v1"
    );

    const geminiPresent = !!safeObject(providers.gemini).api_key_present;
    const nvidiaPresent = !!safeObject(providers.nvidia_api).api_key_present;
    $("gemini-status").textContent = geminiPresent ? "CONNECTED" : "NOT CONNECTED";
    $("nvidia-status").textContent = nvidiaPresent ? "CONNECTED" : "NOT CONNECTED";
    $("settings-state").innerHTML =
      '<span class="signal-dot"></span> ' +
      esc(String(data.runtime && data.runtime.director_provider || director.provider || "unknown").toUpperCase());
    $("nvidia-models").innerHTML = "";
    showSettingsMessage(geminiPresent || nvidiaPresent ? "Provider configuration loaded." : "Enter your provider keys here; no terminal is needed.", false);
  }

  async function loadSettings() {
    const response = await fetch(state.apiBase + "/settings", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("settings " + response.status);
    renderSettings(await response.json());
  }

  async function saveSettings() {
    const payload = {
      director: {
        provider: $("director-provider").value,
        model: $("director-model").value.trim(),
        endpoint: $("director-endpoint").value.trim()
      },
      providers: {
        nvidia_api: {
          base_url: $("nvidia-endpoint").value.trim()
        }
      }
    };
    const geminiKey = $("gemini-key").value.trim();
    const nvidiaKey = $("nvidia-key").value.trim();
    if (geminiKey) payload.providers.gemini = { api_key: geminiKey };
    if (nvidiaKey) payload.providers.nvidia_api.api_key = nvidiaKey;

    const button = $("settings-save");
    button.disabled = true;
    showSettingsMessage("Saving and applying provider configuration…", false);
    try {
      const response = await fetch(state.apiBase + "/settings", {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data && data.error ? data.error : "settings " + response.status);
      $("gemini-key").value = "";
      $("nvidia-key").value = "";
      renderSettings(data);
      showSettingsMessage("Configuration saved and applied. Aurora is ready to use the selected Director.", false);
    } catch (error) {
      showSettingsMessage(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function testProvider(provider) {
    showSettingsMessage("Testing " + provider.toUpperCase() + "…", false);
    const response = await fetch(state.apiBase + "/settings/test", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ provider: provider })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data && data.error ? data.error : "provider test failed");
    if (provider === "nvidia_api") {
      const models = safeArray(data.models);
      $("nvidia-models").innerHTML = models.length
        ? '<div class="settings-model-title">DISCOVERED MODELS</div>' +
          models.slice(0, 40).map(function (model) {
            return '<button type="button" class="settings-model" data-model="' + esc(model) + '">' + esc(model) + '</button>';
          }).join("")
        : '<div class="empty-state">No model IDs returned by NVIDIA.</div>';
      document.querySelectorAll(".settings-model").forEach(function (button) {
        button.addEventListener("click", function () {
          $("director-model").value = button.getAttribute("data-model") || "";
          $("director-provider").value = "nvidia_api";
        });
      });
    }
    showSettingsMessage(provider.toUpperCase() + " connection verified.", false);
  }

  function setupSettings() {
    $("settings-save").addEventListener("click", function () {
      saveSettings();
    });
    $("settings-reload").addEventListener("click", function () {
      loadSettings().catch(function (error) { showSettingsMessage(error.message, true); });
    });
    $("gemini-test").addEventListener("click", function () {
      testProvider("gemini").catch(function (error) { showSettingsMessage(error.message, true); });
    });
    $("nvidia-test").addEventListener("click", function () {
      testProvider("nvidia_api").catch(function (error) { showSettingsMessage(error.message, true); });
    });
  }

  async function openInspector(eyebrow, title, promise) {
    $("inspector-eyebrow").textContent = String(eyebrow).toUpperCase();
    $("inspector-title").textContent = title;
    $("inspector-body").innerHTML = '<div class="empty-state">Loading authoritative state…</div>';
    $("inspector").classList.add("open");
    $("inspector").setAttribute("aria-hidden", "false");
    try {
      const data = await promise;
      $("inspector-body").innerHTML = detailHtml(data);
    } catch (error) {
      $("inspector-body").innerHTML = '<div class="empty-state">Inspector unavailable: ' + esc(error.message) + "</div>";
    }
  }

  async function fetchProject(projectId) {
    const response = await fetch(state.apiBase + "/projects/" + encodeURIComponent(projectId), { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("project " + response.status);
    return response.json();
  }

  async function fetchAgent(role) {
    const response = await fetch(state.apiBase + "/agents/" + encodeURIComponent(role), { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("agent " + response.status);
    return response.json();
  }

  function detailHtml(data) {
    const blocks = [];
    Object.entries(safeObject(data)).slice(0, 50).forEach(function (entry) {
      const key = entry[0];
      const value = entry[1];
      blocks.push(
        '<div class="detail-block"><div><span>' + esc(key) + "</span><strong>" +
        esc(value && typeof value === "object" ? JSON.stringify(value) : value) +
        "</strong></div></div>"
      );
    });
    return blocks.join("") || '<div class="empty-state">No authoritative inspector data.</div>';
  }

  function closeInspector() {
    $("inspector").classList.remove("open");
    $("inspector").setAttribute("aria-hidden", "true");
  }

  async function ensureConversationSession() {
    if (state.conversationSessionId) return state.conversationSessionId;
    const response = await fetch(state.conversationApiBase + "/session", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({})
    });
    if (!response.ok) throw new Error("conversation session " + response.status);
    const data = await response.json();
    if (!data || typeof data.session_id !== "string" || !data.session_id) throw new Error("invalid conversation session contract");
    state.conversationSessionId = data.session_id;
    return data.session_id;
  }

  async function loadConversationHistory() {
    const body = $("chat-history-body");
    body.innerHTML = '<div class="empty-state">Loading durable conversation…</div>';
    try {
      const sessionId = await ensureConversationSession();
      const response = await fetch(
        state.conversationApiBase + "/" + encodeURIComponent(sessionId) + "/turns",
        { headers: { Accept: "application/json" } }
      );
      if (!response.ok) throw new Error("history " + response.status);
      const data = await response.json();
      const turns = safeArray(data.turns);
      if (!turns.length) {
        body.innerHTML = '<div class="empty-state">No conversation turns yet.</div>';
        return;
      }
      body.innerHTML = "";
      turns.forEach(function (turn) {
        const item = document.createElement("article");
        item.className = "history-turn " + (turn.role === "user" ? "user" : "assistant");
        item.innerHTML = '<div class="role">' + esc(String(turn.role || "assistant").toUpperCase()) + "</div><p>" + esc(turn.content) + "</p>";
        body.appendChild(item);
      });
      body.scrollTop = body.scrollHeight;
    } catch (error) {
      body.innerHTML = '<div class="empty-state">Conversation history unavailable: ' + esc(error.message) + "</div>";
    }
  }

  function openHistory() {
    $("chat-history").classList.add("open");
    $("chat-history").setAttribute("aria-hidden", "false");
    loadConversationHistory();
  }

  function closeHistory() {
    $("chat-history").classList.remove("open");
    $("chat-history").setAttribute("aria-hidden", "true");
  }

  async function sendConversationMessage(rawText) {
    const content = String(rawText == null ? "" : rawText).trim();
    if (!content || state.conversationInFlight) return;
    state.conversationInFlight = true;
    const send = $("director-send");
    send.disabled = true;
    if (state.avatarVoice) state.avatarVoice.setThinking();
    $("avatar-transcript").textContent = "Aurora is thinking…";

    try {
      const sessionId = await ensureConversationSession();
      const response = await fetch(
        state.conversationApiBase + "/" + encodeURIComponent(sessionId) + "/turn",
        {
          method: "POST",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body: JSON.stringify({ user_input: content })
        }
      );
      if (!response.ok) {
        let message = "conversation " + response.status;
        try {
          const errorBody = await response.json();
          if (errorBody && errorBody.error) message = errorBody.error;
        } catch (_) {}
        throw new Error(message);
      }
      const data = await response.json();
      if (!data || typeof data.response_text !== "string") throw new Error("invalid Director response contract");
      $("director-message").value = "";
      $("avatar-transcript").textContent = data.response_text;

      if (state.avatarVoice && state.avatarVoice.capabilities.speech_output) {
        try {
          state.avatarVoice.speak(data.response_text);
        } catch (_) {
          // Voice output is optional; preserve the authoritative text response.
          state.avatarVoice.setIdle();
        }
      } else if (state.avatarVoice) {
        state.avatarVoice.setIdle();
      }
    } catch (error) {
      $("avatar-transcript").textContent = "Director conversation unavailable: " + error.message;
      if (state.avatarVoice) state.avatarVoice.setDisconnected();
    } finally {
      state.conversationInFlight = false;
      send.disabled = false;
    }
  }

  function setupConversation() {
    $("director-send").addEventListener("click", function () {
      sendConversationMessage($("director-message").value);
    });
    $("director-message").addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendConversationMessage(event.currentTarget.value);
      }
    });
    $("chat-expand").addEventListener("click", openHistory);
    $("chat-history-close").addEventListener("click", closeHistory);
    $("history-backdrop").addEventListener("click", closeHistory);
  }

  function setupAvatarPortrait() {
    const image = $("director-portrait-image");
    const placeholder = $("portrait-placeholder");
    if (!image || !placeholder) return;
    const source = String(state.portraitUrl || "").trim();
    if (!source) return;
    image.addEventListener("load", function () { image.hidden = false; placeholder.hidden = true; });
    image.addEventListener("error", function () { image.hidden = true; placeholder.hidden = false; image.removeAttribute("src"); });
    image.src = source;
  }

  function setupAvatarVoice() {
    const adapterApi = window.AuroraAvatarVoiceAdapter;
    const root = $("director-portrait");
    if (!adapterApi || !root) return;

    state.avatarVoice = new adapterApi.AvatarVoiceAdapter({
      root,
      transcriptTarget: $("avatar-transcript"),
      statusTarget: $("avatar-voice-status"),
      language: "es-ES",
      onState: function (nextState) {
        if (nextState === adapterApi.STATES.ERROR) {
          $("avatar-transcript").textContent = "Avatar presentation unavailable; text output remains active.";
        }
      },
      onTranscript: function (payload) {
        if (payload.final) {
          $("avatar-transcript").textContent = "Transcribed: " + payload.text + " · sending to Director";
          sendConversationMessage(payload.text);
        }
      }
    });

    $("avatar-listen").addEventListener("click", async function () {
      if (!state.avatarVoice.capabilities.speech_input) {
        state.avatarVoice.setState(adapterApi.STATES.ERROR);
        $("avatar-transcript").textContent = "Speech input is unavailable; use text conversation.";
        return;
      }
      try {
        await state.avatarVoice.startListening();
      } catch (_) {
        $("avatar-transcript").textContent = "Microphone unavailable; use text conversation.";
      }
    });
    $("avatar-stop").addEventListener("click", function () {
      state.avatarVoice.stopListening();
      state.avatarVoice.stopSpeaking();
    });
  }

  function scheduleRefresh() {
    if (state.refreshTimer) window.clearTimeout(state.refreshTimer);
    if (document.hidden) return;
    state.refreshTimer = window.setTimeout(function () { loadSnapshot({ quiet: true }); }, state.retryDelay);
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      if (state.refreshTimer) window.clearTimeout(state.refreshTimer);
      state.refreshTimer = null;
    } else {
      state.retryDelay = 10000;
      loadSnapshot({ quiet: true });
    }
  });

  $("refresh").addEventListener("click", function () {
    state.retryDelay = 10000;
    loadSnapshot();
  });
  $("inspector-close").addEventListener("click", closeInspector);
  $("inspector-backdrop").addEventListener("click", closeInspector);
  window.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      closeInspector();
      closeHistory();
    }
  });

  setupNavigation();
  setupConversation();
  setupAvatarPortrait();
  setupAvatarVoice();
  setupSettings();

  ensureConversationSession().catch(function (error) {
    $("avatar-transcript").textContent = "Conversation service unavailable: " + error.message;
    if (state.avatarVoice) state.avatarVoice.setDisconnected();
  });

  setView("home");
  loadSnapshot();
})();