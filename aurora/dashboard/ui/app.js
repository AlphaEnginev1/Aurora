(() => {
  "use strict";

  const state = {
    snapshot: null,
    apiBase: window.AURORA_DASHBOARD_API_BASE || "/api/dashboard"
  };

  const $ = (id) => document.getElementById(id);

  function esc(value) {
    return String(value == null ? "—" : value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function dateText(value) {
    if (!value) return "unknown time";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  }

  function setUnknown(reason) {
    $("system-status").textContent = "SYSTEM UNKNOWN";
    $("system-status").className = "status-pill unknown";
    $("freshness").textContent = reason;
  }

  async function loadSnapshot() {
    const button = $("refresh");
    button.disabled = true;
    try {
      const response = await fetch(state.apiBase + "/snapshot", {
        headers: { Accept: "application/json" }
      });
      if (!response.ok) throw new Error("dashboard API " + response.status);
      state.snapshot = await response.json();
      render();
    } catch (error) {
      setUnknown("Snapshot unavailable: " + error.message);
    } finally {
      button.disabled = false;
    }
  }

  function render() {
    const data = state.snapshot;
    if (!data) return;

    const readiness = data.director && data.director.readiness || "UNKNOWN";
    $("system-status").textContent = readiness === "READY" ? "SYSTEM READY" : "SYSTEM UNKNOWN";
    $("system-status").className = "status-pill " + (readiness === "READY" ? "ready" : "unknown");
    $("freshness").textContent = "Snapshot " + dateText(data.generated_at);

    $("director-name").textContent = data.director && data.director.identity || "Aurora Director";
    const unknown = data.director && data.director.context_unknown_fields || [];
    $("director-readiness").textContent = unknown.length
      ? "Context has " + unknown.length + " unknown field(s)."
      : "Authoritative Director context loaded.";

    const configured = data.director && data.director.configured_models || [];
    $("director-model").textContent = configured.length
      ? (configured[0].id || configured[0].model_id || "configured")
      : "unknown";
    $("director-context").textContent = data.director && data.director.context_manifest_id
      ? data.director.context_manifest_id.slice(0, 12)
      : "unknown";

    const projects = Array.isArray(data.projects) ? data.projects : [];
    const agents = data.organization && Array.isArray(data.organization.agents)
      ? data.organization.agents
      : [];

    $("project-count").textContent = projects.length;
    $("agent-count").textContent = agents.length + " agents";
    $("active-project-count").textContent =
      projects.filter(function (p) { return p.status === "active"; }).length + " active";

    renderAgents(agents);
    renderProjects(projects);
    renderMailbox(data.mailbox && data.mailbox.messages || []);
    renderModels(data.models || [], data.providers || []);
    renderSkills(data.skills || []);
    renderRuntime(data.runtime || {});
  }

  function renderAgents(agents) {
    const target = $("agent-orbit");
    target.innerHTML = "";
    if (!agents.length) {
      target.innerHTML = '<div class="empty-state">No registered agents.</div>';
      return;
    }

    const core = document.createElement("div");
    core.className = "mailbox-core";
    core.innerHTML = "📬<small>SHARED</small>";
    target.appendChild(core);

    const positions = agents.map(function (_, index) {
      const angle = (-Math.PI / 2) + (index * Math.PI * 2 / agents.length);
      const radiusX = Math.min(38, 44 - agents.length * 1.5);
      const radiusY = 37;
      return {
        left: 50 + Math.cos(angle) * radiusX,
        top: 50 + Math.sin(angle) * radiusY
      };
    });

    agents.forEach(function (agent, index) {
      const node = document.createElement("button");
      node.type = "button";
      node.className = "agent-node";
      node.style.left = positions[index].left + "%";
      node.style.top = positions[index].top + "%";
      node.innerHTML =
        '<div class="avatar">👤</div>' +
        "<strong>" + esc(agent.role) + "</strong>" +
        "<span>" + esc(agent.department) + "</span>";
      node.addEventListener("click", function () {
        openInspector(
          "Agent Inspector",
          agent.role,
          fetchAgent(agent.role)
        );
      });
      target.appendChild(node);
    });
  }

  function renderProjects(projects) {
    const target = $("projects-list");
    target.innerHTML = "";
    if (!projects.length) {
      target.innerHTML = '<div class="empty-state">No projects registered.</div>';
      return;
    }

    projects.forEach(function (project) {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "list-item";
      item.innerHTML =
        '<div class="list-item-head">' +
        "<strong>" + esc(project.name || project.project_id) + "</strong>" +
        '<span class="badge">' + esc(project.status) + "</span>" +
        "</div>" +
        "<p>" + esc(project.objective) + "</p>";
      item.addEventListener("click", function () {
        openInspector(
          "Project Inspector",
          project.name || project.project_id,
          fetchProject(project.project_id)
        );
      });
      target.appendChild(item);
    });
  }

  function renderMailbox(messages) {
    $("mailbox-count").textContent = String(messages.length);
    const target = $("mailbox-list");
    target.innerHTML = "";
    if (!messages.length) {
      target.innerHTML = '<div class="empty-state">No mailbox messages.</div>';
      return;
    }

    messages.slice(-12).reverse().forEach(function (message) {
      const item = document.createElement("div");
      item.className = "timeline-item";
      item.innerHTML =
        "<strong>" + esc(message.kind) + " · " + esc(message.source) + "</strong>" +
        "<span>" + esc(message.content) + " · " + esc(dateText(message.created_at)) + "</span>";
      target.appendChild(item);
    });
  }

  function renderModels(models, providers) {
    $("model-count").textContent = models.length + " models";
    const target = $("model-list");
    target.innerHTML = "";
    if (!models.length) {
      target.innerHTML = '<div class="empty-state">No models registered.</div>';
      return;
    }

    models.slice(0, 10).forEach(function (model) {
      const item = document.createElement("div");
      item.className = "list-item";
      item.innerHTML =
        '<div class="list-item-head">' +
        "<strong>" + esc(model.model_id) + "</strong>" +
        '<span class="badge">' + esc(model.provider) + "</span>" +
        "</div>" +
        "<p>" + esc((model.capabilities || []).join(", ") || "no capabilities") +
        " · health " + esc(model.healthy) + "</p>";
      target.appendChild(item);
    });

    if (providers.length) {
      const footer = document.createElement("div");
      footer.className = "muted";
      footer.style.padding = "4px 2px";
      footer.textContent = providers.length + " registered provider(s)";
      target.appendChild(footer);
    }
  }

  function renderSkills(skills) {
    const target = $("skills-list");
    target.innerHTML = "";
    if (!skills.length) {
      target.innerHTML = '<div class="empty-state">No skills registered.</div>';
      return;
    }

    skills.slice(0, 10).forEach(function (skill) {
      const item = document.createElement("div");
      item.className = "list-item";
      item.innerHTML =
        '<div class="list-item-head">' +
        "<strong>" + esc(skill.name || skill.skill_id) + "</strong>" +
        '<span class="badge">v' + esc(skill.version) + "</span>" +
        "</div>" +
        "<p>" + esc(skill.mission) + "</p>";
      target.appendChild(item);
    });
  }

  function renderRuntime(runtime) {
    const target = $("runtime-summary");
    const counts = runtime.task_status_counts || {};
    target.innerHTML =
      '<div class="metric"><span>Missions</span><strong>' + esc(runtime.mission_count || 0) + "</strong></div>" +
      '<div class="metric"><span>Tasks</span><strong>' + esc(runtime.task_count || 0) + "</strong></div>" +
      '<div class="metric"><span>Running</span><strong>' + esc(counts.running || 0) + "</strong></div>" +
      '<div class="metric"><span>Failed</span><strong>' + esc(counts.failed || 0) + "</strong></div>";
  }

  async function openInspector(eyebrow, title, promise) {
    $("inspector-eyebrow").textContent = eyebrow.toUpperCase();
    $("inspector-title").textContent = title;
    $("inspector-body").innerHTML = '<div class="empty-state">Loading authoritative state…</div>';
    $("inspector").classList.add("open");
    $("inspector").setAttribute("aria-hidden", "false");

    try {
      const data = await promise;
      $("inspector-body").innerHTML = detailHtml(data);
    } catch (error) {
      $("inspector-body").innerHTML =
        '<div class="empty-state">Inspector unavailable: ' + esc(error.message) + "</div>";
    }
  }

  async function fetchProject(projectId) {
    const response = await fetch(
      state.apiBase + "/projects/" + encodeURIComponent(projectId),
      { headers: { Accept: "application/json" } }
    );
    if (!response.ok) throw new Error("project " + response.status);
    return response.json();
  }

  async function fetchAgent(role) {
    const response = await fetch(
      state.apiBase + "/agents/" + encodeURIComponent(role),
      { headers: { Accept: "application/json" } }
    );
    if (!response.ok) throw new Error("agent " + response.status);
    return response.json();
  }

  function detailHtml(data) {
    const blocks = [];
    Object.entries(data || {}).slice(0, 40).forEach(function (entry) {
      const key = entry[0];
      const value = entry[1];

      if (value && typeof value === "object") {
        blocks.push(
          '<div class="detail-block"><strong>' + esc(key) + '</strong>' +
          '<div class="detail-grid"><div><span>Value</span><strong>' +
          esc(JSON.stringify(value)) +
          "</strong></div></div></div>"
        );
      } else {
        blocks.push(
          '<div class="detail-block"><div><span>' + esc(key) +
          "</span><strong>" + esc(value) + "</strong></div></div>"
        );
      }
    });
    return blocks.join("") || '<div class="empty-state">No authoritative inspector data.</div>';
  }

  function closeInspector() {
    $("inspector").classList.remove("open");
    $("inspector").setAttribute("aria-hidden", "true");
  }

  $("refresh").addEventListener("click", loadSnapshot);
  $("inspector-close").addEventListener("click", closeInspector);
  $("inspector-backdrop").addEventListener("click", closeInspector);
  window.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeInspector();
  });

  loadSnapshot();
})();
