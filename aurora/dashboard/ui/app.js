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
    pendingConversationRequest: null,
    activeView: "home",
    settings: null,
    github: null,
    health: null,
    healthTimer: null,
    healthInFlight: false,
    pendingAttachments: [],
    roadmapProfiles: {},
    roadmapInFlight: false,
    roadmapHasBaseline: false,
    roadmapSeenCompletionEvents: new Set(),
    roadmapSelectedRole: null,
    roadmapSelectedModelId: null,
    roadmapSelectedStationKey: null,
    roadmapContext: null,
    roadmapDeliveryCount: 0,
    roadmapDeliveryItems: []
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
    if (state.refreshTimer) window.clearTimeout(state.refreshTimer);
    state.refreshTimer = null;
    document.querySelectorAll("[data-view-panel]").forEach(function (panel) {
      panel.classList.toggle("active", panel.getAttribute("data-view-panel") === view);
    });
    if (view === "health") loadHealth().catch(function (error) {
      renderHealthError(error);
    });
    if (view === "settings") loadSettings().catch(function (error) {
      showSettingsMessage("Settings unavailable: " + error.message, true);
    });
    if (view === "roadmap") {
      renderRoadmap(state.snapshot);
      loadRoadmapDetails().catch(function (error) { renderRoadmapError(error); });
    }
    document.querySelectorAll("[data-nav-target]").forEach(function (button) {
      if (button.classList.contains("rail-button")) {
        button.classList.toggle("active", button.getAttribute("data-nav-target") === view);
      }
    });
    scheduleRefresh();
    if (view === "roadmap") loadSnapshot({ quiet: true });
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
    renderRoadmap(data);
    if (state.activeView === "roadmap") {
      loadRoadmapDetails().catch(function (error) { renderRoadmapError(error); });
    }
  }

  async function loadHealth() {
    if (state.healthInFlight) return;
    state.healthInFlight = true;
    try {
      const response = await fetch(state.apiBase + "/health", { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("health API " + response.status);
      const next = await response.json();
      if (!next || typeof next !== "object" || !next.overall || !Array.isArray(next.components)) {
        throw new Error("invalid health contract");
      }
      state.health = next;
      renderHealth(next);
    } catch (error) {
      renderHealthError(error);
      throw error;
    } finally {
      state.healthInFlight = false;
      if (state.activeView === "health" && !document.hidden) {
        if (state.healthTimer) window.clearTimeout(state.healthTimer);
        state.healthTimer = window.setTimeout(loadHealth, 10000);
      }
    }
  }

  function healthClass(status) {
    return String(status || "UNKNOWN").toLowerCase();
  }

  function renderHealth(data) {
    const summary = safeObject(data.summary);
    const overall = String(data.overall || "UNKNOWN").toUpperCase();
    const badge = $("health-overall-badge");
    badge.textContent = overall;
    badge.className = "health-header-badge " + healthClass(overall);
    $("health-overall-title").textContent = overall === "HEALTHY" ? "Aurora is healthy" : "Aurora requires attention";
    $("health-overall-reason").textContent =
      overall === "HEALTHY"
        ? "All directly observable health signals are healthy."
        : "The panel preserves degraded and unknown signals instead of masking them.";
    $("health-component-count").textContent = String(summary.components || 0) + " signals";
    $("health-agent-count").textContent = String(safeArray(data.agents).length) + " agents";
    $("health-incident-count").textContent = String(safeArray(data.incidents).length) + " incidents";

    const metrics = [
      ["HEALTHY", summary.healthy || 0],
      ["DEGRADED", summary.degraded || 0],
      ["CRITICAL", summary.critical || 0],
      ["UNKNOWN", summary.unknown || 0],
      ["STALE TASKS", summary.stale_tasks || 0],
      ["FAILED TASKS", summary.failed_tasks || 0]
    ];
    $("health-metrics").innerHTML = metrics.map(function (item) {
      return '<div class="health-metric"><span>' + esc(item[0]) + '</span><strong>' + esc(item[1]) + '</strong></div>';
    }).join("");

    const monitoring = safeObject(data.monitoring);
    $("health-monitoring-note").textContent = monitoring.truth_rule || "Health is based only on directly observed signals.";

    const components = safeArray(data.components);
    $("health-components").innerHTML = components.map(function (component) {
      return '<button class="health-component ' + healthClass(component.status) + '" type="button" data-health-component="' + esc(component.key) + '">' +
        '<div class="health-component-top"><span>' + esc(component.layer) + '</span><strong>' + esc(component.status) + '</strong></div>' +
        '<h3>' + esc(component.name) + '</h3><p>' + esc(component.reason) + '</p></button>';
    }).join("") || '<div class="empty-state">No component health signals.</div>';
    document.querySelectorAll("[data-health-component]").forEach(function (button) {
      button.addEventListener("click", function () {
        const item = components.find(function (component) { return component.key === button.getAttribute("data-health-component"); });
        if (item) openInspector("Health Signal", item.name, Promise.resolve(item));
      });
    });

    const agents = safeArray(data.agents);
    $("health-agents").innerHTML = agents.map(function (agent) {
      return '<button class="health-agent-card ' + healthClass(agent.status) + '" type="button" data-health-agent="' + esc(agent.role) + '">' +
        '<div class="health-agent-top"><strong>' + esc(agent.role) + '</strong><span>' + esc(agent.status) + '</span></div>' +
        '<p>' + esc(agent.reason) + '</p>' +
        '<small>' + esc(agent.recent_failures || 0) + ' recent failure signal(s) · ' + esc(agent.recent_events || 0) + ' recent event(s)</small></button>';
    }).join("") || '<div class="empty-state">No agents registered.</div>';

    const incidents = safeArray(data.incidents);
    $("health-incidents").innerHTML = incidents.map(function (incident) {
      return '<button class="health-incident ' + healthClass(incident.status) + '" type="button" data-health-incident="' + esc(incident.incident_id) + '">' +
        '<div class="health-incident-head"><strong>' + esc(incident.status) + ' · ' + esc(incident.event_type) + '</strong><span>' + esc(dateText(incident.created_at)) + '</span></div>' +
        '<div class="health-incident-context">' + esc(incident.agent_role || incident.task_id || incident.component || "runtime") + '</div>' +
        '<p>' + esc(incident.error) + '</p>' +
        '<small>mission ' + esc(incident.mission_id || "—") + ' · task ' + esc(incident.task_id || "—") + '</small></button>';
    }).join("") || '<div class="empty-state">No incident history recorded.</div>';
    document.querySelectorAll("[data-health-incident]").forEach(function (button) {
      button.addEventListener("click", function () {
        const item = incidents.find(function (incident) { return incident.incident_id === button.getAttribute("data-health-incident"); });
        if (item) openInspector("Incident Detail", item.event_type || "Incident", Promise.resolve(item));
      });
    });
  }

  function renderHealthError(error) {
    const badge = $("health-overall-badge");
    if (!badge) return;
    badge.textContent = "UNKNOWN";
    badge.className = "health-header-badge unknown";
    $("health-overall-title").textContent = "Health telemetry unavailable";
    $("health-overall-reason").textContent = "Aurora could not obtain a health snapshot: " + error.message;
    $("health-components").innerHTML = '<div class="empty-state">No current health state can be asserted.</div>';
    $("health-agents").innerHTML = '<div class="empty-state">Agent health cannot be asserted.</div>';
    $("health-incidents").innerHTML = '<div class="empty-state">Incident history could not be loaded.</div>';
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

  function roadMapStatus(profile) {
    if (!profile || profile.__unavailable) return { key: "unknown", label: "STATE UNKNOWN", task: null };
    const currentTasks = safeArray(profile.current_tasks);
    const running = currentTasks.find(function (task) { return String(task.status || "").toLowerCase() === "running"; });
    const pending = currentTasks.find(function (task) { return String(task.status || "").toLowerCase() === "pending"; });
    const recent = safeArray(profile.recent_executions);
    const failed = recent.find(function (task) { return String(task.status || "").toLowerCase() === "failed"; });
    if (running) return { key: "working", label: "WORKING", task: running };
    if (pending) return { key: "queued", label: "QUEUED", task: pending };
    if (failed && failed.last_error) return { key: "failed", label: "IDLE · LAST FAILURE", task: failed };
    return { key: "idle", label: "IDLE", task: null };
  }

  function roadmapValueId(value) {
    if (typeof value === "string") return value.trim();
    if (!value || typeof value !== "object") return "";
    return String(
      value.model_id || value.id || value.selected_model || value.model || value.name || ""
    ).trim();
  }

  function roadmapAgentModelIds(agent) {
    const role = String(agent.role || agent.agent_id || "");
    const profile = state.roadmapProfiles[role];
    const values = safeArray(profile && profile.model_preferences).concat(
      safeArray(agent.model_preferences)
    );
    return Array.from(new Set(values.map(roadmapValueId).filter(Boolean)));
  }

  function roadmapModelCatalog(data) {
    const catalog = new Map();
    function add(raw, registered) {
      const id = roadmapValueId(raw);
      if (!id) return;
      const existing = catalog.get(id);
      if (existing) {
        if (registered) existing.registered = true;
        if ((!existing.provider || existing.provider === "not specified") && raw && typeof raw === "object" && raw.provider) {
          existing.provider = raw.provider;
        }
        if (raw && typeof raw === "object") Object.assign(existing, raw, { model_id: id, registered: existing.registered || registered });
        return;
      }
      const details = raw && typeof raw === "object" ? raw : {};
      catalog.set(id, Object.assign({
        model_id: id,
        provider: details.provider || (registered ? "not specified" : "not in model registry"),
        registered: Boolean(registered),
        configured_only: !registered
      }, details, { model_id: id, registered: Boolean(registered) }));
    }

    safeArray(data && data.models).forEach(function (model) { add(model, true); });
    const candidateState = safeObject(data && data.model_candidates);
    safeArray(candidateState.candidates).forEach(function (candidate) {
      const id = roadmapValueId(candidate.model_id);
      if (!id || catalog.has(id)) return;
      add({
        model_id: id,
        provider: candidate.provider_id || "not reported",
        endpoint: candidate.endpoint,
        enabled: candidate.enabled,
        healthy: candidate.healthy,
        available: candidate.available,
        capabilities: candidate.capabilities,
        specialties: candidate.specialties,
        context_window: candidate.context_window,
        pricing_tier: candidate.pricing_tier,
        quality_score: candidate.quality_score,
        latency_score: candidate.latency_score,
        cost_score: candidate.cost_score,
        candidate_state: candidate.state,
        candidate_id: candidate.candidate_id,
        discovered_at: candidate.discovered_at
      }, false);
    });
    const org = safeObject(data && data.organization);
    safeArray(org.departments).forEach(function (department) {
      safeArray(department.model_preferences).forEach(function (model) { add(model, false); });
    });
    safeArray(org.agents).forEach(function (agent) {
      safeArray(agent.model_preferences).forEach(function (model) { add(model, false); });
      const role = String(agent.role || agent.agent_id || "");
      safeArray(state.roadmapProfiles[role] && state.roadmapProfiles[role].model_preferences)
        .forEach(function (model) { add(model, false); });
    });
    safeArray(safeObject(data && data.director).configured_models).forEach(function (model) { add(model, false); });
    const activity = safeObject(data && data.model_activity);
    safeArray(activity.active_executions).forEach(function (execution) {
      const id = roadmapValueId(execution.model_id || execution.selected_model || execution.model);
      if (id) add({ model_id: id, provider: execution.provider || "not reported" }, false);
    });
    return catalog;
  }

  function roadmapDepartmentSlot(name) {
    const key = String(name || "").toLowerCase().replace(/[_-]+/g, " ");
    if (/research|discovery|science/.test(key)) return "research";
    if (/engineering|software|develop|coding|computer/.test(key)) return "engineering";
    if (/data|knowledge|memory|retrieval/.test(key)) return "data";
    if (/quality|review|test|audit|verification/.test(key)) return "quality";
    if (/operations|orchestrat|runtime|support|delivery/.test(key)) return "operations";
    if (/security|governance|policy|compliance/.test(key)) return "security";
    if (/learning|skill|evaluation|evolution/.test(key)) return "learning";
    if (/tooling|tools|integration|capability|workbench/.test(key)) return "tooling";
    return null;
  }

  function roadmapBuildContext(data) {
    const org = safeObject(data.organization);
    const agents = safeArray(org.agents);
    const departments = safeArray(org.departments);
    const modelsById = roadmapModelCatalog(data);
    const groups = [];
    const assignedRoles = new Set();

    departments.forEach(function (department) {
      const members = agents.filter(function (agent) {
        return safeArray(department.agent_ids).includes(agent.agent_id) ||
          safeArray(department.agent_ids).includes(agent.role) ||
          String(agent.department || "").toLowerCase() === String(department.department_id || "").toLowerCase();
      });
      members.forEach(function (agent) { assignedRoles.add(String(agent.role || agent.agent_id)); });
      groups.push({ department: department, agents: members, modelIds: [] });
    });

    agents.filter(function (agent) {
      return !assignedRoles.has(String(agent.role || agent.agent_id));
    }).forEach(function (agent) {
      const key = String(agent.department || "unassigned");
      let group = groups.find(function (item) {
        return String(item.department.department_id || item.department.name || "unassigned") === key;
      });
      if (!group) {
        group = {
          department: {
            department_id: key, mission: "Department metadata not available.",
            agent_ids: [], model_preferences: [], allowed_capabilities: []
          },
          agents: [], modelIds: []
        };
        groups.push(group);
      }
      group.agents.push(agent);
    });

    const assignedModelIds = new Set();
    groups.forEach(function (group) {
      const ids = safeArray(group.department.model_preferences).map(roadmapValueId);
      group.agents.forEach(function (agent) {
        ids.push.apply(ids, roadmapAgentModelIds(agent));
      });
      group.modelIds = Array.from(new Set(ids.filter(function (id) { return modelsById.has(id); })));
      group.modelIds.forEach(function (id) { assignedModelIds.add(id); });
    });

    const directorModelIds = Array.from(new Set(
      safeArray(safeObject(data.director).configured_models).map(roadmapValueId).filter(function (id) {
        return modelsById.has(id);
      })
    ));
    directorModelIds.forEach(function (id) { assignedModelIds.add(id); });

    return {
      snapshot: data,
      agents: agents,
      groups: groups,
      modelsById: modelsById,
      assignedModelIds: assignedModelIds,
      directorModelIds: directorModelIds,
      unassignedModelIds: Array.from(modelsById.keys()).filter(function (id) {
        return !assignedModelIds.has(id);
      }).sort()
    };
  }

  function roadMapModelStatus(model, context) {
    const modelId = roadmapValueId(model);
    const activity = safeObject(safeObject(context.snapshot).model_activity);
    const allExecutions = safeArray(activity.active_executions);
    const executions = allExecutions.filter(function (item) {
      return roadmapValueId(item.model_id || item.selected_model || item.model) === modelId;
    });
    if (executions.length) {
      return { key: "working", label: "INFERENCE RUNNING", executions: executions, busyAgents: [] };
    }

    if (model.registered === false && model.candidate_state) {
      if (model.candidate_state === "rejected") {
        return { key: "unavailable", label: "REJECTED · GOVERNANCE", executions: [], busyAgents: [] };
      }
      const candidateLabel = model.candidate_state === "synced"
        ? "SYNCED FLAG · NOT IN REGISTRY"
        : "STAGED · NOT ROUTABLE";
      return { key: "candidate", label: candidateLabel, executions: [], busyAgents: [] };
    }
    if (model.registered === false || model.configured_only) {
      return { key: "unknown", label: "NOT IN CATALOG", executions: [], busyAgents: [] };
    }
    if (model.enabled === false) {
      return { key: "unavailable", label: "DISABLED", executions: [], busyAgents: [] };
    }
    if (model.available === false || model.healthy === false) {
      return { key: "unavailable", label: "UNAVAILABLE", executions: [], busyAgents: [] };
    }
    const eligibleModelIds = safeArray(activity.eligible_model_ids);
    if (activity.eligibility_state === "authoritative" && !eligibleModelIds.includes(modelId)) {
      return { key: "unavailable", label: "NOT ROUTABLE NOW", executions: [], busyAgents: [] };
    }

    const roles = context.agents.filter(function (agent) {
      return roadmapAgentModelIds(agent).includes(modelId);
    });
    const busyAgents = roles.filter(function (agent) {
      const profile = state.roadmapProfiles[String(agent.role || agent.agent_id || "")];
      return safeArray(profile && profile.current_tasks).some(function (task) {
        return String(task.status || "").toLowerCase() === "running";
      });
    });
    if (busyAgents.length) {
      return {
        key: "busy",
        label: "AGENT TASK ACTIVE · MODEL UNCONFIRMED",
        executions: [],
        busyAgents: busyAgents
      };
    }
    if (activity.state !== "authoritative") {
      return { key: "unknown", label: "USAGE UNKNOWN", executions: [], busyAgents: [] };
    }
    return { key: "ready", label: "READY · NOT EXECUTING", executions: [], busyAgents: [] };
  }

  function roadmapStationKey(modelId, placement) {
    return String(placement || "reserve") + "::" + String(modelId);
  }

  function roadmapModelStation(modelId, group, context) {
    const model = context.modelsById.get(modelId) || { model_id: modelId, registered: false };
    const modelRoles = context.agents.filter(function (agent) {
      return roadmapAgentModelIds(agent).includes(modelId);
    });
    const groupRoles = group
      ? group.agents.filter(function (agent) { return roadmapAgentModelIds(agent).includes(modelId); })
      : [];
    const status = roadMapModelStatus(model, context);
    const placement = group
      ? (group.department.department_id || group.department.name || "department")
      : "reserve";
    const stationKey = roadmapStationKey(modelId, placement);
    const selected = state.roadmapSelectedStationKey === stationKey;
    const provider = model.provider || "provider not recorded";
    const roleNames = modelRoles.map(function (agent) { return String(agent.role || agent.agent_id); });
    const roomRoles = groupRoles.map(function (agent) { return String(agent.role || agent.agent_id); });
    const roleText = roomRoles.length
      ? roomRoles.join(" · ")
      : (modelRoles.length ? modelRoles.map(function (agent) { return String(agent.role || agent.agent_id); }).join(" · ") : "Unassigned model");
    return '<button class="floorplan-model-station' + (selected ? ' selected' : '') +
        '" type="button" data-roadmap-model="' + esc(modelId) + '" data-roadmap-station-key="' + esc(stationKey) +
        '" data-model-state="' + esc(status.key) +
        '" data-agent-roles="' + esc(JSON.stringify(roomRoles.length ? roomRoles : roleNames)) +
        '" aria-pressed="' + String(selected) + '" aria-label="Inspect model ' + esc(modelId) + ', ' + esc(status.label) + '">' +
      '<span class="floorplan-workstation-art" aria-hidden="true">' +
        '<span class="floorplan-desk-surface"></span><span class="floorplan-monitor"><i></i><i></i><i></i></span>' +
        '<span class="floorplan-chair"></span><span class="floorplan-avatar"></span><i class="floorplan-status-led"></i>' +
      '</span>' +
      '<span class="floorplan-model-title"><strong>' + esc(modelId) + '</strong></span>' +
      '<span class="floorplan-provider">' + esc(provider) + '</span>' +
      '<span class="floorplan-model-status"><i></i>' + esc(status.label) + '</span>' +
      '<span class="floorplan-model-roles">' + esc(roleText) + '</span>' +
    '</button>';
  }

  function roadmapDepartmentMarkup(group, context) {
    const department = group.department || {};
    const name = department.name || department.department_id || "Unassigned";
    const activeModels = group.modelIds.filter(function (id) {
      return roadMapModelStatus(context.modelsById.get(id), context).key === "working";
    }).length;
    const agentPills = group.agents.length
      ? group.agents.map(function (agent) {
          const role = String(agent.role || agent.agent_id || "unknown");
          const status = roadMapStatus(state.roadmapProfiles[role]);
          return '<span class="floorplan-agent-pill" data-agent-state="' + esc(status.key) + '">' +
            '<i></i>' + esc(role) + '</span>';
        }).join("")
      : '<span class="floorplan-muted">No agents registered in this department.</span>';
    const stations = group.modelIds.length
      ? group.modelIds.map(function (id) { return roadmapModelStation(id, group, context); }).join("")
      : '<div class="floorplan-empty-room">No model preference is registered for this department.</div>';
    return '<header class="floorplan-room-header"><div><span>DEPARTMENT</span><h3>' + esc(name) +
      '</h3><p>' + esc(department.mission || "Department mission not recorded.") + '</p></div>' +
      '<strong class="floorplan-room-count">' + group.modelIds.length + ' models · ' + activeModels + ' active</strong></header>' +
      '<div class="floorplan-agent-pills">' + agentPills + '</div>' +
      '<div class="floorplan-model-grid">' + stations + '</div>';
  }

  function roadmapCell(label, value, detail, className) {
    return '<section class="model-inspector-cell' + (className ? ' ' + className : '') + '">' +
      '<span>' + esc(label) + '</span><strong>' + esc(value || "Not reported") + '</strong>' +
      (detail ? '<p>' + detail + '</p>' : '') + '</section>';
  }

  function renderRoadmap(data) {
    if (!data) return;
    const context = roadmapBuildContext(data);
    state.roadmapContext = context;
    const slotOrder = ["research", "engineering", "data", "quality", "operations", "security", "learning", "tooling"];
    const slotGroups = new Map();
    const overflowGroups = [];

    context.groups.forEach(function (group) {
      const department = group.department || {};
      const key = roadmapDepartmentSlot(department.department_id || department.name || "");
      if (key && !slotGroups.has(key)) slotGroups.set(key, group);
      else overflowGroups.push(group);
    });
    overflowGroups.slice().forEach(function (group) {
      const free = slotOrder.find(function (key) { return !slotGroups.has(key); });
      if (free) {
        slotGroups.set(free, group);
        overflowGroups.splice(overflowGroups.indexOf(group), 1);
      }
    });

    slotOrder.forEach(function (slot) {
      const node = $("roadmap-room-" + slot);
      if (!node) return;
      const group = slotGroups.get(slot);
      if (!group) {
        node.hidden = true;
        node.innerHTML = "";
        return;
      }
      node.hidden = false;
      node.innerHTML = roadmapDepartmentMarkup(group, context);
    });

    const directorModels = $("roadmap-director-models");
    if (directorModels) {
      directorModels.innerHTML = context.directorModelIds.length
        ? context.directorModelIds.map(function (id) {
            const stationKey = roadmapStationKey(id, "director");
            const selected = state.roadmapSelectedStationKey === stationKey;
            return '<button class="floorplan-director-model-chip' + (selected ? ' selected' : '') +
              '" type="button" data-roadmap-model="' + esc(id) + '" data-roadmap-station-key="' + esc(stationKey) +
              '" data-model-state="' + esc(roadMapModelStatus(context.modelsById.get(id), context).key) +
              '" aria-pressed="' + String(selected) + '">' + esc(id) + '</button>';
          }).join("")
        : '<span class="floorplan-muted">No Director model exposed by current policy.</span>';
    }

    const extras = [];
    overflowGroups.forEach(function (group) {
      const department = group.department || {};
      extras.push('<section class="floorplan-room floorplan-extra-department"><header class="floorplan-room-header"><div><span>ADDITIONAL DEPARTMENT</span><h3>' +
        esc(department.name || department.department_id || "Unassigned") + '</h3><p>' +
        esc(department.mission || "Department mission not recorded.") + '</p></div><strong class="floorplan-room-count">' +
        group.modelIds.length + ' models</strong></header><div class="floorplan-agent-pills">' +
        (group.agents.length ? group.agents.map(function (agent) {
          const role = String(agent.role || agent.agent_id || "unknown");
          return '<span class="floorplan-agent-pill"><i></i>' + esc(role) + '</span>';
        }).join("") : '<span class="floorplan-muted">No agents registered.</span>') +
        '</div><div class="floorplan-model-grid">' +
        (group.modelIds.length ? group.modelIds.map(function (id) { return roadmapModelStation(id, group, context); }).join("") :
          '<div class="floorplan-empty-room">No model preference recorded.</div>') + '</div></section>');
    });

    if (context.unassignedModelIds.length) {
      extras.push('<section class="floorplan-room floorplan-unassigned-room"><header class="floorplan-room-header"><div><span>RESERVE / DISCOVERY POOL</span><h3>Unassigned, reserve & staged models</h3><p>Registered reserve models and discovery candidates without a department preference. Candidates are not routable until promoted.</p></div><strong class="floorplan-room-count">' +
        context.unassignedModelIds.length + ' models</strong></header><div class="floorplan-model-grid">' +
        context.unassignedModelIds.map(function (id) { return roadmapModelStation(id, null, context); }).join("") +
        '</div></section>');
    }
    const extraTarget = $("roadmap-office-grid");
    if (extraTarget) {
      extraTarget.innerHTML = extras.join("");
      extraTarget.hidden = !extras.length;
    }

    const registeredCount = safeArray(data.models).length;
    const activity = safeObject(data.model_activity);
    const activeExecutions = safeArray(activity.active_executions);
    $("roadmap-department-count").textContent = String(context.groups.length);
    $("roadmap-model-count").textContent = String(registeredCount);
    const candidateRows = safeArray(safeObject(data.model_candidates).candidates);
    const registeredIds = new Set(safeArray(data.models).map(roadmapValueId));
    $("roadmap-candidate-count").textContent = safeObject(data.model_candidates).state === "authoritative"
      ? String(candidateRows.filter(function (candidate) { return !registeredIds.has(roadmapValueId(candidate.model_id)); }).length)
      : "—";
    $("roadmap-active-count").textContent = activity.state === "authoritative" ? String(activeExecutions.length) : "—";
    $("roadmap-delivery-count").textContent = String(state.roadmapDeliveryCount);
    $("roadmap-mailbox-total").textContent = String(state.roadmapDeliveryCount);

    const liveBadge = $("roadmap-live-state");
    if (liveBadge) {
      liveBadge.className = "roadmap-live-badge " + (activity.state === "authoritative" ? "healthy" : "unknown");
      liveBadge.innerHTML = '<span class="roadmap-live-dot"></span> ' +
        esc(activity.state === "authoritative" ? "LIVE MODEL ACTIVITY" : "MODEL ACTIVITY UNKNOWN");
    }
    $("roadmap-last-update").textContent = "Snapshot · " + dateText(data.generated_at);
    const activityNote = $("roadmap-data-note");
    if (activityNote) {
      activityNote.textContent = activity.state === "authoritative"
        ? "Green LEDs are direct in-flight ModelRouter executions. Ready/idle means no inference call was active in this snapshot; agent task state is shown separately."
        : "Aurora has not exposed authoritative live model execution telemetry. Model availability may be known, but active-use status remains unknown.";
    }

    const modelIds = Array.from(context.modelsById.keys()).sort();
    const activeModelId = activeExecutions.length ? roadmapValueId(activeExecutions[0].model_id || activeExecutions[0].selected_model) : "";
    if (!state.roadmapSelectedModelId || !context.modelsById.has(state.roadmapSelectedModelId)) {
      state.roadmapSelectedModelId = (activeModelId && context.modelsById.has(activeModelId)) ? activeModelId : (modelIds[0] || null);
      state.roadmapSelectedStationKey = null;
    }

    // A single model ID may be assigned to multiple departments. Select one
    // rendered workstation occurrence, not every tile sharing that model ID.
    const modelButtons = Array.from(document.querySelectorAll("[data-roadmap-model]"));
    let selectedButton = modelButtons.find(function (button) {
      return button.getAttribute("data-roadmap-station-key") === state.roadmapSelectedStationKey;
    });
    if (!selectedButton) {
      selectedButton = modelButtons.find(function (button) {
        return button.getAttribute("data-roadmap-model") === state.roadmapSelectedModelId;
      }) || modelButtons.find(function (button) {
        return button.getAttribute("data-roadmap-model") === activeModelId;
      }) || modelButtons.find(function (button) {
        return button.classList.contains("floorplan-model-station");
      }) || modelButtons[0] || null;
      state.roadmapSelectedStationKey = selectedButton
        ? selectedButton.getAttribute("data-roadmap-station-key")
        : null;
      if (selectedButton) {
        state.roadmapSelectedModelId = selectedButton.getAttribute("data-roadmap-model");
      }
    }

    modelButtons.forEach(function (button) {
      const isSelected = Boolean(state.roadmapSelectedStationKey) &&
        button.getAttribute("data-roadmap-station-key") === state.roadmapSelectedStationKey;
      button.classList.toggle("selected", isSelected);
      button.setAttribute("aria-pressed", String(isSelected));
      button.addEventListener("click", function () {
        state.roadmapSelectedModelId = button.getAttribute("data-roadmap-model");
        state.roadmapSelectedStationKey = button.getAttribute("data-roadmap-station-key");
        renderRoadmap(state.snapshot);
      }, { once: true });
    });

    renderRoadmapInspector(state.roadmapSelectedModelId);
    renderRoadmapDeliveries();
  }

  function renderRoadmapInspector(modelId) {
    const title = $("roadmap-inspector-title");
    const body = $("roadmap-inspector-body");
    const led = $("roadmap-inspector-led");
    const context = state.roadmapContext || (state.snapshot ? roadmapBuildContext(state.snapshot) : null);
    if (!title || !body || !led || !context || !modelId) {
      if (title) title.textContent = "Select an AI model";
      if (body) body.innerHTML = '<div class="empty-state">No model is available to inspect in the current authoritative snapshot.</div>';
      if (led) led.className = "workstation-led unknown";
      return;
    }

    const model = context.modelsById.get(modelId) || { model_id: modelId, registered: false };
    const status = roadMapModelStatus(model, context);
    const activeExecutions = status.executions || [];
    const activity = safeObject(context.snapshot.model_activity);
    const assignedAgents = context.agents.filter(function (agent) {
      return roadmapAgentModelIds(agent).includes(modelId);
    });
    const assignedDepartments = context.groups.filter(function (group) {
      return group.modelIds.includes(modelId);
    });
    const currentTaskRows = [];
    assignedAgents.forEach(function (agent) {
      const role = String(agent.role || agent.agent_id || "unknown");
      const profile = state.roadmapProfiles[role];
      safeArray(profile && profile.current_tasks).filter(function (task) {
        return String(task.status || "").toLowerCase() === "running";
      }).forEach(function (task) {
        currentTaskRows.push({ role: role, department: agent.department || "Unassigned", task: task });
      });
    });
    const activityRows = activeExecutions.map(function (execution) {
      const role = String(execution.role || "Director / runtime");
      const task = safeArray(state.roadmapProfiles[role] && state.roadmapProfiles[role].current_tasks)
        .find(function (item) { return String(item.task_id || "") === String(execution.task_id || ""); });
      const what = task && task.objective
        ? task.objective
        : (execution.task_type || (execution.task_id ? "Task " + execution.task_id : "Director / live inference"));
      return '<div class="model-work-row"><strong>' + esc(role) + ' · ' + esc(execution.task_id || execution.request_id || "request") +
        '</strong><p>' + esc(what) + '</p><small>Started: ' + esc(dateText(execution.started_at)) +
        (execution.project_id ? ' · project ' + esc(execution.project_id) : '') + '</small></div>';
    });
    const modelPerformance = safeArray(safeObject(context.snapshot.model_intelligence).performance)
      .filter(function (row) { return String(row.model || "") === modelId; })
      .slice(0, 4);
    const performanceHtml = modelPerformance.length
      ? modelPerformance.map(function (row) {
          return '<div class="model-work-row"><strong>' + esc(row.task_type || row.skill_id || "Observed execution") +
            ' · score ' + esc(row.score == null ? "unknown" : Number(row.score).toFixed(3)) + '</strong><p>' +
            esc(row.evidence_state || "Evidence state not recorded") + ' · ' + esc(dateText(row.created_at)) + '</p></div>';
        }).join("")
      : '<p>No model-performance rows have been recorded for this model.</p>';
    const provider = String(model.provider || "not recorded");
    const eligibleModelIds = safeArray(activity.eligible_model_ids);
    const routeEligibility = activity.eligibility_state === "authoritative"
      ? (eligibleModelIds.includes(modelId) ? "Eligible now" : "Not eligible now")
      : "Unknown";
    const modelReadiness = model.candidate_state
      ? ("Discovery candidate · " + model.candidate_state + " · not in active model registry")
      : (model.registered === false
        ? "Configured reference · not in registry"
        : (model.enabled === false ? "Disabled" :
          (model.available === false || model.healthy === false ? "Unavailable / unhealthy" : "Enabled in model registry")));
    const usageDetail = activityRows.length
      ? activityRows.join("")
      : (currentTaskRows.length
        ? currentTaskRows.map(function (row) {
            return '<div class="model-work-row"><strong>' + esc(row.role) + ' · ' +
              esc(row.task.task_id || "running task") + '</strong><p>' + esc(row.task.objective || "Task running; selected model is not exposed by this task record.") +
              '</p><small>Agent status confirms running task; this does not prove this model is executing.</small></div>';
          }).join("")
        : '<p>' + esc(activity.state === "authoritative"
          ? "No in-flight inference call for this model was observed in the current snapshot."
          : "Live model execution telemetry is not available, so current use cannot be verified.") + '</p>');

    title.textContent = modelId;
    led.className = "workstation-led " + status.key;
    led.setAttribute("aria-label", status.label);
    const statusLine = '<div class="model-inspector-status" data-model-state="' + esc(status.key) + '"><i></i><strong>' +
      esc(status.label) + '</strong><span>' + esc(activeExecutions.length + " active call(s)") + '</span></div>';
    body.innerHTML =
      statusLine +
      '<div class="model-inspector-grid">' +
      roadmapCell("MODEL ID", modelId, model.registered === false ? '<p>Not present in the canonical model registry; shown because Aurora references it in configuration.</p>' : '') +
      roadmapCell("PROVIDER", provider, model.endpoint ? '<p>Endpoint: ' + esc(model.endpoint) + '</p>' : '<p>Endpoint not exposed by the model record.</p>') +
      roadmapCell("REGISTRY / HEALTH", modelReadiness, '<p>Enabled: ' + esc(String(model.enabled)) + ' · Healthy: ' + esc(String(model.healthy)) +
        ' · Available: ' + esc(String(model.available)) + ' · Router: ' + esc(routeEligibility) + '</p>' +
        (model.candidate_state ? '<p>Candidate state: ' + esc(model.candidate_state) + ' · discovered ' + esc(dateText(model.discovered_at)) + '</p>' : '')) +
      roadmapCell("WHERE ASSIGNED", assignedDepartments.map(function (group) {
        return group.department.name || group.department.department_id || "Department";
      }).join(" · ") || (context.directorModelIds.includes(modelId) ? "Director policy" : "Not assigned to a department"),
        '<p>' + esc(assignedAgents.map(function (agent) { return String(agent.role || agent.agent_id); }).join(", ") || "No agent model preference maps to this model.") + '</p>') +
      roadmapCell("CURRENT WORK", activeExecutions.length ? "Executing now" :
        (currentTaskRows.length ? "Assigned agent task in progress · model not attributed" :
          (activity.state === "authoritative" ? "Not executing at snapshot time" : "Unknown")),
        usageDetail, "model-inspector-wide") +
      roadmapCell("CAPABILITIES", safeArray(model.capabilities).join(" · ") || "Not listed",
        '<p>Specialties: ' + esc(safeArray(model.specialties).join(", ") || "Not listed") + '</p>') +
      roadmapCell("CONTEXT WINDOW", model.context_window ? Number(model.context_window).toLocaleString() + " tokens" : "Not reported",
        '<p>Pricing tier: ' + esc(model.pricing_tier || "not recorded") + '</p>') +
      roadmapCell("MODEL SCORES", "Quality " + (model.quality_score == null ? "—" : Number(model.quality_score).toFixed(2)) +
        " · Latency " + (model.latency_score == null ? "—" : Number(model.latency_score).toFixed(2)) +
        " · Cost " + (model.cost_score == null ? "—" : Number(model.cost_score).toFixed(2)),
        '<p>Scores are registry values, not proof of current use.</p>') +
      roadmapCell("RECENT PERFORMANCE", modelPerformance.length + " recorded row(s)", performanceHtml, "model-inspector-wide") +
      '</div>';
  }

  function renderRoadmapDeliveries() {
    const target = $("roadmap-deliveries-list");
    if (!target) return;
    if (!state.roadmapDeliveryItems.length) {
      target.innerHTML = '<div class="empty-state">No new completion events observed during this session. Older history is not animated retroactively.</div>';
      return;
    }
    target.innerHTML = state.roadmapDeliveryItems.slice(0,8).map(function (item) {
      return '<div class="roadmap-delivery-item"><strong>' + esc(item.role) + ' → Director Inbox</strong><span>' +
        esc(item.event.task_id || item.event.event_type || "Completion event") + ' · ' +
        esc(item.event.event_type || "completion") + ' · ' + esc(dateText(item.event.created_at)) + '</span></div>';
    }).join("");
  }

  function triggerRoadmapDelivery(role) {
    const scene = $("roadmap-house");
    const station = Array.from(document.querySelectorAll("[data-agent-roles]")).find(function (item) {
      try {
        return JSON.parse(item.getAttribute("data-agent-roles") || "[]").includes(String(role));
      } catch (_) {
        return false;
      }
    });
    const worker = station && station.querySelector(".floorplan-avatar");
    const mailbox = $("roadmap-mailbox-slot");
    if (!scene || !worker || !mailbox || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const sceneRect = scene.getBoundingClientRect();
    const sourceRect = worker.getBoundingClientRect();
    const targetRect = mailbox.getBoundingClientRect();
    const actor = document.createElement("div");
    actor.className = "office-delivery-actor";
    actor.setAttribute("aria-hidden", "true");
    actor.innerHTML = '<span class="worker-head"></span><span class="worker-hair"></span><span class="worker-body"></span><span class="delivery-package"></span>';
    const startX = sourceRect.left + sourceRect.width / 2 - sceneRect.left - 15;
    const startY = sourceRect.top + sourceRect.height / 2 - sceneRect.top - 18;
    const endX = targetRect.left + targetRect.width / 2 - sceneRect.left - 15;
    const endY = targetRect.top + targetRect.height / 2 - sceneRect.top - 18;
    actor.style.left = startX + "px";
    actor.style.top = startY + "px";
    scene.appendChild(actor);
    window.requestAnimationFrame(function () {
      actor.style.transform = "translate(" + (endX - startX) + "px," + (endY - startY) + "px) scale(.74)";
      actor.style.opacity = "0";
    });
    window.setTimeout(function () { actor.remove(); }, 1500);
    mailbox.classList.add("mail-received");
    window.setTimeout(function () { mailbox.classList.remove("mail-received"); }, 1200);
  }

  async function loadRoadmapDetails() {
    if (state.roadmapInFlight || state.activeView !== "roadmap" || !state.snapshot) return;
    const agents = safeArray(safeObject(state.snapshot.organization).agents);
    if (!agents.length) {
      renderRoadmap(state.snapshot);
      return;
    }
    state.roadmapInFlight = true;
    try {
      const fresh = await Promise.all(agents.map(async function (agent) {
        const role = String(agent.role || agent.agent_id || "");
        try {
          const response = await fetch(state.apiBase + "/agents/" + encodeURIComponent(role), {
            headers: { Accept: "application/json", "Cache-Control": "no-cache" },
            cache: "no-store"
          });
          if (!response.ok) throw new Error("agent detail " + response.status);
          return { role: role, profile: await response.json() };
        } catch (error) {
          return { role: role, profile: { __unavailable: true, error: error.message } };
        }
      }));
      const completed = [];
      const currentKeys = new Set();
      fresh.forEach(function (record) {
        state.roadmapProfiles[record.role] = record.profile;
        safeArray(record.profile.events).forEach(function (event) {
          const type = String(event.event_type || "").toLowerCase();
          if (!/(complete|completed|succeed|succeeded|finish|finished|deliver|delivered)/.test(type)) return;
          const key = record.role + ":" + String(event.event_id || (event.task_id + ":" + type + ":" + event.created_at));
          currentKeys.add(key);
          if (state.roadmapHasBaseline && !state.roadmapSeenCompletionEvents.has(key)) {
            completed.push({ role: record.role, event: event });
          }
        });
      });
      currentKeys.forEach(function (key) { state.roadmapSeenCompletionEvents.add(key); });
      const firstPass = !state.roadmapHasBaseline;
      state.roadmapHasBaseline = true;
      if (!firstPass && completed.length) {
        completed.slice(-8).forEach(function (item) {
          state.roadmapDeliveryCount += 1;
          state.roadmapDeliveryItems.unshift(item);
        });
      }
      renderRoadmap(state.snapshot);
      if (!firstPass && completed.length) {
        const roles = Array.from(new Set(completed.slice(-4).map(function (item) { return item.role; })));
        window.setTimeout(function () { roles.forEach(triggerRoadmapDelivery); }, 90);
      }
    } finally {
      state.roadmapInFlight = false;
    }
  }

  function renderRoadmapError(error) {
    const badge = $("roadmap-live-state");
    if (!badge) return;
    badge.className = "roadmap-live-badge unknown";
    badge.innerHTML = '<span class="roadmap-live-dot"></span> LIVE STATE UNAVAILABLE';
    $("roadmap-last-update").textContent = error && error.message ? error.message : "Refresh to retry";
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
    $("director-provider").value = String(director.provider || "router");
    $("director-model").value = String(director.model || "router");
    $("director-endpoint").value = String(director.endpoint || "https://integrate.api.nvidia.com/v1");
    $("settings-state").innerHTML =
      '<span class="signal-dot"></span> ' +
      esc(String(data.runtime && data.runtime.director_provider || director.provider || "unknown").toUpperCase());
    showSettingsMessage(
      "Runtime settings loaded. Provider credentials are managed in the provider credential vault.",
      false
    );
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
      }
    };
    const button = $("settings-save");
    button.disabled = true;
    showSettingsMessage("Saving and applying Director routing settings…", false);
    try {
      const response = await fetch(state.apiBase + "/settings", {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data && data.error ? data.error : "settings " + response.status);
      renderSettings(data);
      showSettingsMessage("Director routing settings saved and applied.", false);
      return true;
    } catch (error) {
      showSettingsMessage(error.message, true);
      return false;
    } finally {
      button.disabled = false;
    }
  }

async function testProvider(provider) {
    if (provider !== "nvidia_api") {
      showSettingsMessage("Aurora uses NVIDIA only.", true);
      return;
    }
    // Save the exact values shown in Settings before discovery. Otherwise this
    // test can use a stale credential/registry snapshot from daemon startup.
    const saved = await saveSettings();
    if (!saved) return;
    showSettingsMessage("Checking NVIDIA's live model catalog…", false);
    const response = await fetch(state.apiBase + "/settings/test", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ provider: "nvidia_api" })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data && data.error ? data.error : "NVIDIA provider test failed");

    const models = safeArray(data.models);
    $("nvidia-models").innerHTML = models.length
      ? '<div class="settings-model-title">DISCOVERED FREE NVIDIA MODELS</div>' +
        models.map(function (model) {
          return '<button type="button" class="settings-model" data-model="' + esc(model) + '">' + esc(model) + '</button>';
        }).join("")
      : '<div class="empty-state">No free NVIDIA model IDs returned.</div>';
    document.querySelectorAll(".settings-model").forEach(function (button) {
      button.addEventListener("click", function () {
        $("director-model").value = button.getAttribute("data-model") || "";
        $("director-provider").value = "nvidia_api";
      });
    });
    showSettingsMessage(
      "Catalog discovery completed · " + Number(data.staged_count || 0) +
        " free model(s) discovered and staged. This does not test chat inference; send a message to verify generation.",
      false
    );
  }

  function setChatStatus(message, error) {
    const target = $("chat-status");
    if (target) {
      target.textContent = message;
      target.classList.toggle("error", !!error);
    }
    if (error && $("settings-message")) showSettingsMessage(message, true);
  }

  function renderAttachmentList() {
    const target = $("attachment-list");
    if (!target) return;
    target.hidden = !state.pendingAttachments.length;
    target.innerHTML = state.pendingAttachments.map(function (item, index) {
      return '<div class="attachment-chip" title="' + esc(item.name) + '">' +
        '<span aria-hidden="true">▤</span><span class="attachment-chip-name">' + esc(item.name) + '</span>' +
        '<button class="attachment-chip-remove" type="button" data-remove-attachment="' + index + '" aria-label="Remove ' + esc(item.name) + '" title="Remove attachment">×</button></div>';
    }).join("");
    target.querySelectorAll("[data-remove-attachment]").forEach(function (button) {
      button.addEventListener("click", function () {
        const index = Number(button.getAttribute("data-remove-attachment"));
        if (!Number.isInteger(index) || index < 0 || index >= state.pendingAttachments.length) return;
        state.pendingAttachments.splice(index, 1);
        renderAttachmentList();
        setChatStatus(state.pendingAttachments.length
          ? state.pendingAttachments.length + " attachment(s) ready."
          : "Conversation ready. Your messages go to Aurora Director.", false);
      });
    });
  }

  function uploadFingerprint(file) {
    const value = [file.name, file.size, file.lastModified].join("\\u0000");
    let hash = 2166136261;
    for (let index = 0; index < value.length; index += 1) {
      hash ^= value.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(16) + "-" + value.length;
  }

  function stableUploadRequest(file) {
    const fingerprint = uploadFingerprint(file);
    const storageKey = "aurora.pendingUploadRequest." + fingerprint;
    try {
      const saved = JSON.parse(window.sessionStorage.getItem(storageKey) || "null");
      if (saved && saved.fingerprint === fingerprint && typeof saved.request_id === "string") {
        return { requestId: saved.request_id, storageKey: storageKey, fingerprint: fingerprint };
      }
    } catch (_) {}
    const requestId = "upload-" + (
      window.crypto && typeof window.crypto.randomUUID === "function"
        ? window.crypto.randomUUID()
        : Date.now().toString(36) + "-" + Math.random().toString(36).slice(2)
    );
    const pending = { requestId: requestId, storageKey: storageKey, fingerprint: fingerprint };
    try {
      window.sessionStorage.setItem(storageKey, JSON.stringify({
        fingerprint: fingerprint,
        request_id: requestId
      }));
    } catch (_) {}
    return pending;
  }

  function clearStableUploadRequest(pending) {
    if (!pending) return;
    try {
      const saved = JSON.parse(window.sessionStorage.getItem(pending.storageKey) || "null");
      if (saved && saved.request_id === pending.requestId) {
        window.sessionStorage.removeItem(pending.storageKey);
      }
    } catch (_) {}
  }

  async function uploadAurora(files) {
    const selected = Array.from(files || []);
    if (!selected.length) return;
    const button = $("attach-file");
    button.disabled = true;
    setChatStatus("Uploading " + selected.length + " attachment(s) to Aurora…", false);
    try {
      for (const file of selected) {
        if (!file.size) throw new Error(file.name + " is empty.");
        if (file.size > 25 * 1024 * 1024) {
          throw new Error(file.name + " exceeds the 25 MiB per-file limit.");
        }
        const pendingRequest = stableUploadRequest(file);
        const response = await fetch(state.apiBase + "/artifacts", {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": file.type || "application/octet-stream",
            "X-Aurora-Filename": encodeURIComponent(file.name),
            "X-Aurora-Request-ID": pendingRequest.requestId
          },
          body: await file.arrayBuffer()
        });
        // Keep the same key only if the network fails before an HTTP response.
        // Any response means the server has classified this request (including
        // payload mismatch); a later intentional upload gets a new key.
        clearStableUploadRequest(pendingRequest);
        let data = {};
        try { data = await response.json(); } catch (_) {}
        if (!response.ok) throw new Error(data && data.error ? data.error : "upload failed for " + file.name);
        state.pendingAttachments.push({
          name: file.name,
          size: file.size,
          mission_id: data.mission_id || "",
          artifact: data.artifact || null
        });
      }
      renderAttachmentList();
      setChatStatus(state.pendingAttachments.length + " attachment(s) ready. They'll be referenced in the next message.", false);
    } catch (error) {
      setChatStatus("Attachment upload failed: " + error.message, true);
    } finally {
      button.disabled = false;
      $("aurora-file-input").value = "";
    }
  }
  async function updateAurora() {
    const button = $("update-aurora");
    button.disabled = true;
    showSettingsMessage("Updating Aurora… the dashboard will restart automatically.", false);

    try {
      const response = await fetch(state.apiBase + "/update", {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({})
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data && data.error ? data.error : "update request failed");

      const startedAt = Date.now();
      const poll = async function () {
        try {
          const statusResponse = await fetch(state.apiBase + "/update/status", {
            headers: { Accept: "application/json" },
            cache: "no-store"
          });
          const status = await statusResponse.json();
          if (status && status.state === "ready") {
            showSettingsMessage(
              status.message || ("Aurora updated to " + String(status.commit || "").slice(0, 12)),
              false
            );
            button.disabled = false;
            loadSnapshot({ quiet: true });
            return;
          }
          if (status && (status.state === "failed" || status.state === "blocked")) {
            throw new Error(status.message || ("Update " + status.state));
          }
        } catch (error) {
          if (Date.now() - startedAt > 45000) {
            showSettingsMessage("Update status unavailable: " + error.message, true);
            button.disabled = false;
            return;
          }
        }
        if (Date.now() - startedAt <= 60000) {
          window.setTimeout(poll, 1500);
        } else {
          showSettingsMessage("Aurora update is still in progress. Refresh the dashboard to check again.", false);
          button.disabled = false;
        }
      };

      window.setTimeout(poll, 2500);
    } catch (error) {
      showSettingsMessage(error.message, true);
      button.disabled = false;
    }
  }

  function renderGitHubStatus(data) {
    state.github = data;
    const badge = $("github-status-badge");
    const description = $("github-status-text");
    const connect = $("github-connect");
    const disconnect = $("github-disconnect");
    if (!badge || !description || !connect || !disconnect) return;

    const configured = data.configured === true;
    const connected = data.connected === true;
    badge.textContent = connected ? "CONNECTED" : (configured ? "DISCONNECTED" : "NOT CONFIGURED");
    badge.className = "badge" + (connected ? " success" : "");
    if (connected) {
      description.textContent = "Connected to GitHub as " + String(data.login || data.name || "GitHub account") + ".";
    } else if (configured) {
      description.textContent = "Connect Aurora to your GitHub account so the Director and Workbench can use the authorized connection.";
    } else {
      description.textContent = "GitHub OAuth is not configured. Set AURORA_GITHUB_OAUTH_CLIENT_ID and AURORA_GITHUB_OAUTH_CLIENT_SECRET in the local runtime environment.";
    }
    connect.disabled = !configured;
    connect.textContent = connected ? "Reconnect GitHub" : "Connect GitHub";
    disconnect.disabled = !connected;
  }

  async function loadGitHubStatus() {
    const response = await fetch("/api/github/status", {
      headers: { Accept: "application/json" }
    });
    if (!response.ok) throw new Error("GitHub status " + response.status);
    const data = await response.json();
    if (!data || typeof data.connected !== "boolean" || typeof data.configured !== "boolean") {
      throw new Error("invalid GitHub status contract");
    }
    renderGitHubStatus(data);
    return data;
  }

  function setupGitHubConnection() {
    const connect = $("github-connect");
    const disconnect = $("github-disconnect");
    if (connect) {
      connect.addEventListener("click", function () {
        window.location.assign("/api/github/oauth/start");
      });
    }
    if (disconnect) {
      disconnect.addEventListener("click", async function () {
        disconnect.disabled = true;
        try {
          const response = await fetch("/api/github/disconnect", {
            headers: { Accept: "application/json" }
          });
          let payload = {};
          try {
            payload = await response.json();
          } catch (_) {}
          if (!response.ok) {
            throw new Error(payload && payload.error ? payload.error : "GitHub disconnect " + response.status);
          }
          await loadGitHubStatus();
          showSettingsMessage("GitHub disconnected.", false);
        } catch (error) {
          showSettingsMessage("GitHub disconnect failed: " + error.message, true);
        } finally {
          disconnect.disabled = !(state.github && state.github.connected === true);
        }
      });
    }
  }

  function setupSettings() {
    $("settings-save").addEventListener("click", function () {
      saveSettings();
    });
    $("update-aurora").addEventListener("click", function () {
      updateAurora();
    });
    $("settings-reload").addEventListener("click", function () {
      loadSettings().catch(function (error) { showSettingsMessage(error.message, true); });
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

  function chatWelcomeHtml() {
    return '<div class="chat-welcome" id="chat-welcome">' +
      '<div class="chat-welcome-symbol" aria-hidden="true">✳</div>' +
      '<h3>Ready when you are.</h3>' +
      '<p>Ask Aurora a question, describe a task, or attach documents for review.</p></div>';
  }

  function chatTurnDisplay(turn) {
    const content = String(turn.content || "");
    const marker = "\n\nArchivos adjuntos recibidos mediante la entrada de artefactos de Aurora:";
    const markerIndex = content.indexOf(marker);
    if (turn.role !== "user" || markerIndex < 0) return { text: content, files: [] };
    const block = content.slice(markerIndex + marker.length);
    const files = block.split("\n").filter(function (line) {
      return line.trim().startsWith("- ");
    }).map(function (line) {
      return line.trim().slice(2).replace(/\s+\(mission_id=[^)]+\)$/, "").trim();
    }).filter(Boolean);
    return { text: content.slice(0, markerIndex).trim(), files: files };
  }

  function chatTurnsHtml(turns) {
    if (!turns.length) return chatWelcomeHtml();
    return turns.map(function (turn) {
      const role = turn.role === "user" ? "user" : "assistant";
      const label = role === "user" ? "Y" : "✦";
      const display = chatTurnDisplay(turn);
      const files = display.files.length
        ? '<div class="chat-message-files">' + display.files.map(function (name) {
            return '<span class="chat-message-file">▤ ' + esc(name) + '</span>';
          }).join("") + '</div>'
        : "";
      return '<article class="chat-message ' + role + '">' +
        '<div class="chat-message-avatar" aria-hidden="true">' + label + '</div>' +
        '<div class="chat-message-body">' + esc(display.text) + files + '</div></article>';
    }).join("");
  }
  async function loadConversationHistory() {
    const visibleBody = $("chat-messages");
    const drawerBody = $("chat-history-body");
    if (visibleBody) {
      visibleBody.setAttribute("aria-busy", "true");
      visibleBody.innerHTML = '<div class="empty-state">Loading saved conversation…</div>';
    }
    if (drawerBody) drawerBody.innerHTML = '<div class="empty-state">Loading durable conversation…</div>';
    try {
      const sessionId = await ensureConversationSession();
      const response = await fetch(
        state.conversationApiBase + "/" + encodeURIComponent(sessionId) + "/turns",
        { headers: { Accept: "application/json" } }
      );
      if (!response.ok) throw new Error("history " + response.status);
      const data = await response.json();
      const turns = safeArray(data.turns);
      if (visibleBody) {
        visibleBody.innerHTML = chatTurnsHtml(turns);
        visibleBody.scrollTop = visibleBody.scrollHeight;
        visibleBody.setAttribute("aria-busy", "false");
      }
      if (drawerBody) {
        if (!turns.length) {
          drawerBody.innerHTML = '<div class="empty-state">No conversation turns yet.</div>';
        } else {
          drawerBody.innerHTML = "";
          turns.forEach(function (turn) {
            const item = document.createElement("article");
            item.className = "history-turn " + (turn.role === "user" ? "user" : "assistant");
            item.innerHTML = '<div class="role">' + esc(String(turn.role || "assistant").toUpperCase()) + "</div><p>" + esc(turn.content) + "</p>";
            drawerBody.appendChild(item);
          });
        }
        drawerBody.scrollTop = drawerBody.scrollHeight;
      }
    } catch (error) {
      if (visibleBody) {
        visibleBody.innerHTML = '<div class="chat-message assistant"><div class="chat-message-avatar" aria-hidden="true">!</div><div class="chat-message-body">Conversation history unavailable: ' + esc(error.message) + "</div></div>";
        visibleBody.setAttribute("aria-busy", "false");
      }
      if (drawerBody) drawerBody.innerHTML = '<div class="empty-state">Conversation history unavailable: ' + esc(error.message) + "</div>";
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

  function appendChatMessage(role, text, files, extraClass) {
    const target = $("chat-messages");
    if (!target) return null;
    const welcome = $("chat-welcome");
    if (welcome) welcome.remove();
    const item = document.createElement("article");
    item.className = "chat-message " + role + (extraClass ? " " + extraClass : "");
    const avatar = document.createElement("div");
    avatar.className = "chat-message-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = role === "user" ? "Y" : "✦";
    const body = document.createElement("div");
    body.className = "chat-message-body";
    body.textContent = text;
    item.appendChild(avatar);
    item.appendChild(body);
    if (files && files.length) {
      const fileList = document.createElement("div");
      fileList.className = "chat-message-files";
      files.forEach(function (file) {
        const chip = document.createElement("span");
        chip.className = "chat-message-file";
        chip.textContent = "▤ " + file.name;
        fileList.appendChild(chip);
      });
      body.appendChild(document.createElement("br"));
      body.appendChild(fileList);
    }
    target.appendChild(item);
    target.scrollTop = target.scrollHeight;
    return item;
  }

  function conversationRequestFingerprint(sessionId, content) {
    // A non-reversible, non-security fingerprint lets a pending retry survive
    // reload without storing the prompt itself in sessionStorage.
    const source = sessionId + "\\u0000" + content;
    let hash = 2166136261;
    for (let index = 0; index < source.length; index += 1) {
      hash ^= source.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(16) + "-" + source.length;
  }

  function getStableConversationRequestId(sessionId, content) {
    const fingerprint = conversationRequestFingerprint(sessionId, content);
    const storageKey = "aurora.pendingConversationRequest." + sessionId;
    try {
      const saved = JSON.parse(window.sessionStorage.getItem(storageKey) || "null");
      if (saved && saved.fingerprint === fingerprint && typeof saved.request_id === "string") {
        state.pendingConversationRequest = {
          storageKey: storageKey,
          fingerprint: fingerprint,
          requestId: saved.request_id
        };
        return saved.request_id;
      }
    } catch (_) {}
    const requestId = "request-" + (
      window.crypto && typeof window.crypto.randomUUID === "function"
        ? window.crypto.randomUUID()
        : Date.now().toString(36) + "-" + Math.random().toString(36).slice(2)
    );
    state.pendingConversationRequest = {
      storageKey: storageKey,
      fingerprint: fingerprint,
      requestId: requestId
    };
    try {
      window.sessionStorage.setItem(storageKey, JSON.stringify({
        fingerprint: fingerprint,
        request_id: requestId
      }));
    } catch (_) {}
    return requestId;
  }

  function clearStableConversationRequest(requestId) {
    const pending = state.pendingConversationRequest;
    if (!pending || pending.requestId !== requestId) return;
    try {
      const saved = JSON.parse(window.sessionStorage.getItem(pending.storageKey) || "null");
      if (saved && saved.request_id === requestId) {
        window.sessionStorage.removeItem(pending.storageKey);
      }
    } catch (_) {}
    state.pendingConversationRequest = null;
  }

  async function sendConversationMessage(rawText) {
    const raw = String(rawText == null ? "" : rawText).trim();
    const attachments = state.pendingAttachments.slice();
    if ((!raw && !attachments.length) || state.conversationInFlight) return;

    let content = raw || "Revisa los archivos adjuntos recibidos por Aurora y resume qué has podido extraer. Si no puedes acceder al contenido de algún archivo, indícalo explícitamente.";
    if (attachments.length) {
      content += "\n\nArchivos adjuntos recibidos mediante la entrada de artefactos de Aurora:";
      attachments.forEach(function (item) {
        content += "\n- " + item.name + (item.mission_id ? " (mission_id=" + item.mission_id + ")" : "");
      });
      content += "\nTen en cuenta estos artefactos para responder. No afirmes haber leído un archivo si no puedes acceder realmente a su contenido.";
    }

    state.conversationInFlight = true;
    const send = $("director-send");
    send.disabled = true;
    $("attach-file").disabled = true;
    if (state.avatarVoice) state.avatarVoice.setThinking();
    setChatStatus("Aurora is thinking…", false);
    appendChatMessage("user", raw || "Revisa los archivos adjuntos.", attachments);
    const thinking = appendChatMessage("assistant", "Thinking…", [], "is-thinking");

    try {
      const sessionId = await ensureConversationSession();
      const requestId = getStableConversationRequestId(sessionId, content);
      const response = await fetch(
        state.conversationApiBase + "/" + encodeURIComponent(sessionId) + "/turn",
        {
          method: "POST",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body: JSON.stringify({ user_input: content, request_id: requestId })
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
      // Any structured HTTP response means the server recognized this request
      // key. Clear it so a later intentional turn—even with identical wording—
      // receives a fresh key. Network errors retain it for safe retry.
      clearStableConversationRequest(requestId);
      $("director-message").value = "";
      $("director-message").style.height = "auto";
      state.pendingAttachments = [];
      renderAttachmentList();
      $("avatar-transcript").textContent = data.response_text;
      $("avatar-transcript").classList.remove("error");
      setChatStatus(
        data.state === "in_progress"
          ? "Order already accepted and still running; do not resend it."
          : "Aurora replied.",
        false
      );

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
      await loadConversationHistory();
    } catch (error) {
      if (thinking && thinking.parentNode) thinking.remove();
      appendChatMessage("assistant", "Director conversation unavailable: " + error.message);
      setChatStatus("Director conversation failed: " + error.message, true);
      if (state.avatarVoice) state.avatarVoice.setDisconnected();
    } finally {
      state.conversationInFlight = false;
      send.disabled = false;
      $("attach-file").disabled = false;
    }
  }
  function setupConversation() {
    $("attach-file").addEventListener("click", function () {
      $("aurora-file-input").click();
    });
    $("aurora-file-input").addEventListener("change", function () {
      uploadAurora($("aurora-file-input").files);
    });
    $("director-message").addEventListener("input", function (event) {
      const target = event.currentTarget;
      target.style.height = "auto";
      target.style.height = Math.min(target.scrollHeight, 180) + "px";
    });
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
          setChatStatus("Avatar presentation unavailable; text output remains active.", false);
        }
      },
      onTranscript: function (payload) {
        if (payload.final) {
          $("avatar-transcript").textContent = "Transcribed: " + payload.text + " · sending to Director";
          setChatStatus("Voice input transcribed · sending to Aurora.", false);
          sendConversationMessage(payload.text);
        }
      }
    });

    $("avatar-listen").addEventListener("click", async function () {
      if (!state.avatarVoice.capabilities.speech_input) {
        state.avatarVoice.setState(adapterApi.STATES.ERROR);
        $("avatar-transcript").textContent = "Speech input is unavailable; use text conversation.";
        setChatStatus("Speech input is unavailable; use text conversation.", false);
        return;
      }
      try {
        await state.avatarVoice.startListening();
      } catch (_) {
        $("avatar-transcript").textContent = "Microphone unavailable; use text conversation.";
        setChatStatus("Microphone unavailable; use text conversation.", false);
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
    const refreshInterval = state.activeView === "roadmap"
      ? 2000
      : state.retryDelay;
    state.refreshTimer = window.setTimeout(function () { loadSnapshot({ quiet: true }); }, refreshInterval);
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

  $("health-refresh").addEventListener("click", function () {
    loadHealth().catch(function () {});
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
  setupGitHubConnection();
  loadGitHubStatus().catch(function (error) {
    const status = $("github-status-text");
    if (status) status.textContent = "GitHub status unavailable: " + error.message;
  });
  setupConversation();
  setupAvatarPortrait();
  setupAvatarVoice();
  setupSettings();

  ensureConversationSession().then(function () {
    return loadConversationHistory();
  }).catch(function (error) {
    setChatStatus("Conversation service unavailable: " + error.message, true);
    if (state.avatarVoice) state.avatarVoice.setDisconnected();
  });

  setView("home");
  loadSnapshot();
})();