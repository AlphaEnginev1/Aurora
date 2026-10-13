(() => {
  "use strict";

  const apiBase = (window.AURORA_DASHBOARD_API_BASE || "/api/dashboard");
  const endpoints = {
    nvidia_api: "https://integrate.api.nvidia.com/v1",
    openai: "https://api.openai.com/v1",
    anthropic: "https://api.anthropic.com/v1",
    gemini: "https://generativelanguage.googleapis.com/v1beta/openai/",
    xai: "https://api.x.ai/v1",
    openrouter: "https://openrouter.ai/api/v1",
    groq: "https://api.groq.com/openai/v1",
    deepseek: "https://api.deepseek.com/v1",
    mistral: "https://api.mistral.ai/v1",
    together_ai: "https://api.together.xyz/v1",
    fireworks_ai: "https://api.fireworks.ai/inference/v1",
    perplexity: "https://api.perplexity.ai",
    cohere: "https://api.cohere.com/compatibility/v1",
    ollama: "http://127.0.0.1:11434/v1",
    lm_studio: "http://127.0.0.1:1234/v1",
    custom_openai_compatible: ""
  };
  const labels = {
    nvidia_api: "NVIDIA NIM", openai: "OpenAI", anthropic: "Anthropic / Claude",
    gemini: "Google Gemini", xai: "xAI / Grok", openrouter: "OpenRouter",
    groq: "Groq", deepseek: "DeepSeek", mistral: "Mistral", together_ai: "Together AI",
    fireworks_ai: "Fireworks AI", perplexity: "Perplexity", cohere: "Cohere",
    ollama: "Ollama (local)", lm_studio: "LM Studio (local)",
    custom_openai_compatible: "Custom OpenAI-compatible"
  };
  const $ = (id) => document.getElementById(id);
  const safeObject = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : {};
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

  function setEndpointForSelection() {
    const provider = selectedProvider();
    const config = safeObject(safeObject(window.__auroraProviderSettings).providers)[provider];
    $("credential-endpoint").value = String(config && config.base_url || endpoints[provider] || "");
    $("credential-key").value = "";
    $("credential-key").placeholder = provider === "ollama" || provider === "lm_studio"
      ? "Optional for local endpoint" : "Paste the provider API key";
    const testButton = $("nvidia-test");
    if (testButton) testButton.hidden = provider !== "nvidia_api";
  }

  function renderInventory(data) {
    window.__auroraProviderSettings = data;
    const providers = safeObject(data.providers);
    const rows = Object.entries(providers).map(([id, raw]) => {
      const config = safeObject(raw);
      const present = config.api_key_present === true;
      const local = id === "ollama" || id === "lm_studio";
      const integration = id === "nvidia_api"
        ? "NVIDIA adapter available · test separately"
        : id === "openrouter"
          ? "Runtime activation not yet verified"
          : "Credential storage only · adapter not yet verified";
      return '<tr><td><strong>' + esc(labels[id] || id) + '</strong><small>' + esc(id) + '</small></td>' +
        '<td class="provider-endpoint">' + esc(config.base_url || "—") + '</td>' +
        '<td><span class="credential-state ' + (present ? "present" : "missing") + '">' +
        (present ? "KEY SAVED · ••••••••" : (local ? "NO KEY · LOCAL" : "NO KEY SAVED")) +
        '</span></td><td>' + esc(integration) + '</td></tr>';
    }).sort((a, b) => a.localeCompare(b));
    $("credential-inventory").innerHTML = rows.length
      ? rows.join("")
      : '<tr><td colspan="4">No providers saved yet.</td></tr>';
    setEndpointForSelection();
  }

  async function loadInventory() {
    const response = await fetch(apiBase + "/settings", { headers: { Accept: "application/json" }, cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data && data.error || "Could not load provider settings");
    renderInventory(data);
  }

  async function saveCredential() {
    const provider = selectedProvider();
    const endpoint = $("credential-endpoint").value.trim();
    const key = $("credential-key").value.trim();
    if (!endpoint) {
      message("Enter the API endpoint for this provider.", true);
      return;
    }
    if (!key && provider !== "ollama" && provider !== "lm_studio") {
      message("Enter the API key. An empty field never overwrites an existing key.", true);
      return;
    }
    const button = $("credential-save");
    button.disabled = true;
    message("Saving provider settings…", false);
    try {
      const config = { base_url: endpoint };
      if (key) config.api_key = key;
      const response = await fetch(apiBase + "/settings", {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ providers: { [provider]: config } })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data && data.error || "Could not save provider settings");
      $("credential-key").value = "";
      renderInventory(data);
      message((labels[provider] || provider) + " settings saved. Credential presence was refreshed; inference support has not been verified.", false);
    } catch (error) {
      message(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  function init() {
    const select = $("credential-provider");
    if (!select) return;
    select.addEventListener("change", setEndpointForSelection);
    $("credential-save").addEventListener("click", saveCredential);
    $("credential-clear").addEventListener("click", () => { $("credential-key").value = ""; });
    $("credential-refresh").addEventListener("click", () => loadInventory().catch(error => message(error.message, true)));
    const reload = $("settings-reload");
    if (reload) reload.addEventListener("click", () => window.setTimeout(() => loadInventory().catch(error => message(error.message, true)), 100));
    const nav = document.querySelector('[data-nav-target="settings"]');
    if (nav) nav.addEventListener("click", () => window.setTimeout(() => loadInventory().catch(error => message(error.message, true)), 100));
    loadInventory().catch(error => message(error.message, true));
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();