(() => {
  "use strict";

  const apiBase = window.AURORA_DASHBOARD_API_BASE || "/api/dashboard";
  const state = { catalog: [], byId: Object.create(null) };
  const $ = (id) => document.getElementById(id);
  const safeObject = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const safeArray = (value) => Array.isArray(value) ? value : [];
  const esc = (value) => String(value == null ? "" : value)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");

  function message(text, error) {
    const target = $("credential-message");
    if (!target) return;
    target.textContent = text;
    target.className = "settings-message " + (error ? "error" : "success");
  }

  function selectedProvider() {
    return $("credential-provider").value;
  }

  function definition(providerId) {
    return safeObject(state.byId[providerId]);
  }

  function providerConfig(providerId) {
    return safeObject(safeObject(window.__auroraProviderSettings).providers)[providerId] || {};
  }

  function setEndpointForSelection() {
    const providerId = selectedProvider();
    const metadata = definition(providerId);
    const saved = safeObject(providerConfig(providerId));
    $("credential-endpoint").value = String(saved.base_url || metadata.base_url || "");
    $("credential-key").value = "";
    $("credential-key").placeholder = metadata.auth_required === false
      ? "Optional for this provider" : "Paste the provider API key";
    const testButton = $("nvidia-test");
    if (testButton) testButton.hidden = providerId !== "nvidia_api";
    const genericTestButton = $("credential-test-provider");
    if (genericTestButton) {
      genericTestButton.hidden = providerId === "nvidia_api"
        || metadata.adapter_available !== true
        || metadata.registry_mode === "catalog_only";
      genericTestButton.textContent = "Test " + (metadata.name || providerId) + " connection";
    }
    const stateLabel = $("provider-catalog-status");
    if (stateLabel && providerId) {
      if (metadata.registry_mode === "catalog_only") {
        stateLabel.textContent = "Credential storage only for this provider: its runtime adapter is not implemented yet.";
      } else if (metadata.adapter_available) {
        stateLabel.textContent = "Runtime adapter exists. Saving a key does not verify authentication or live inference.";
      } else {
        stateLabel.textContent = "Provider metadata loaded. Runtime integration is not available yet.";
      }
    }
  }

  function renderProviderSelector(catalog) {
    state.catalog = catalog;
    state.byId = Object.create(null);
    catalog.forEach(function (provider) {
      if (provider && typeof provider.provider_id === "string") {
        state.byId[provider.provider_id] = provider;
      }
    });
    const select = $("credential-provider");
    const previous = select.value;
    select.innerHTML = catalog.map(function (provider) {
      return '<option value="' + esc(provider.provider_id) + '">' + esc(provider.name) + '</option>';
    }).join("");
    select.value = state.byId[previous]
      ? previous
      : (state.byId.nvidia_api ? "nvidia_api" : (catalog[0] ? catalog[0].provider_id : ""));
    if (!select.value) throw new Error("provider catalog is empty");
  }

  function runtimeStatus(metadata) {
    if (!metadata) return "Not present in the provider catalog";
    if (metadata.registry_mode === "catalog_only" || !metadata.adapter_available) {
      return "Credential storage only · adapter not implemented";
    }
    if (metadata.provider_id === "nvidia_api") {
      return "NVIDIA adapter available · connection unverified";
    }
    return "Adapter available · connection unverified";
  }

  function renderInventory(data) {
    const catalog = safeArray(data.provider_catalog);
    if (!catalog.length) throw new Error("Settings API did not return the provider catalog");
    window.__auroraProviderSettings = data;
    renderProviderSelector(catalog);

    const savedProviders = safeObject(data.providers);
    const rows = Object.entries(savedProviders).map(function (entry) {
      const id = entry[0];
      const config = safeObject(entry[1]);
      const metadata = definition(id);
      const present = config.api_key_present === true;
      const credentialText = metadata.auth_required === false && !present
        ? "NO KEY REQUIRED"
        : (present ? "KEY SAVED · ••••••••" : "NO KEY SAVED");
      return '<tr><td><strong>' + esc(metadata.name || id) + '</strong><small>' + esc(id) + '</small></td>' +
        '<td class="provider-endpoint">' + esc(config.base_url || metadata.base_url || "—") + '</td>' +
        '<td><span class="credential-state ' + (present ? "present" : "missing") + '">' + credentialText + '</span></td>' +
        '<td>' + esc(runtimeStatus(metadata)) + '</td></tr>';
    }).sort(function (a, b) { return a.localeCompare(b); });

    $("credential-inventory").innerHTML = rows.length
      ? rows.join("")
      : '<tr><td colspan="4">No providers have been saved yet.</td></tr>';
    setEndpointForSelection();
  }

  async function loadInventory() {
    const response = await fetch(apiBase + "/settings", {
      headers: { Accept: "application/json" },
      cache: "no-store"
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data && data.error || "Could not load provider settings");
    renderInventory(data);
  }

  async function saveCredential() {
    const providerId = selectedProvider();
    const metadata = definition(providerId);
    if (!metadata.provider_id) {
      message("Choose a provider from the validated catalog.", true);
      return;
    }
    const endpoint = $("credential-endpoint").value.trim();
    const key = $("credential-key").value.trim();
    if (!endpoint) {
      message("Enter the API endpoint for this provider.", true);
      return;
    }
    if (metadata.auth_required !== false && !key) {
      message("Enter the API key. An empty field never overwrites an existing key.", true);
      return;
    }
    const button = $("credential-save");
    button.disabled = true;
    message("Saving provider settings…", false);
    try {
      const config = { base_url: endpoint, enabled: true };
      if (key) config.api_key = key;
      const response = await fetch(apiBase + "/settings", {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ providers: { [providerId]: config } })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data && data.error || "Could not save provider settings");
      $("credential-key").value = "";
      renderInventory(data);
      message((metadata.name || providerId) + " settings saved. Credential presence was refreshed; live authentication and inference have not been verified.", false);
    } catch (error) {
      message(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function testSelectedProvider() {
    const providerId = selectedProvider();
    const metadata = definition(providerId);
    const button = $("credential-test-provider");
    if (!metadata.provider_id || metadata.adapter_available !== true || metadata.registry_mode === "catalog_only") {
      message("This provider has no implemented runtime adapter yet; credentials can be stored but no connection test is available.", true);
      return;
    }
    if (providerId === "nvidia_api") {
      message("Use the NVIDIA discovery test for this provider.", true);
      return;
    }
    button.disabled = true;
    message("Checking the saved credential against the provider's model-list endpoint. This does not run inference.", false);
    try {
      const response = await fetch(apiBase + "/settings/test", {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ provider: providerId })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data && data.error || "Provider connection test failed");
      const count = Number(data.model_count || 0);
      message(
        (metadata.name || providerId) + " authenticated successfully; " + count +
        " model ID(s) visible. Chat inference has not been verified by this check.",
        false
      );
    } catch (error) {
      message((metadata.name || providerId) + " connection failed: " + error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  function init() {
    const select = $("credential-provider");
    if (!select) return;
    select.addEventListener("change", setEndpointForSelection);
    $("credential-save").addEventListener("click", saveCredential);
    const genericTestButton = $("credential-test-provider");
    if (genericTestButton) genericTestButton.addEventListener("click", testSelectedProvider);
    $("credential-clear").addEventListener("click", function () {
      $("credential-key").value = "";
    });
    $("credential-refresh").addEventListener("click", function () {
      loadInventory().catch(function (error) { message(error.message, true); });
    });
    const reload = $("settings-reload");
    if (reload) reload.addEventListener("click", function () {
      window.setTimeout(function () {
        loadInventory().catch(function (error) { message(error.message, true); });
      }, 100);
    });
    const nav = document.querySelector('[data-nav-target="settings"]');
    if (nav) nav.addEventListener("click", function () {
      window.setTimeout(function () {
        loadInventory().catch(function (error) { message(error.message, true); });
      }, 100);
    });
    loadInventory().catch(function (error) { message(error.message, true); });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();