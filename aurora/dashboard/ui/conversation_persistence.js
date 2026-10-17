(function (root) {
  "use strict";

  const SESSION_PREFIX = "aurora.conversationSession.v1.";
  const PROJECT_KEY = "aurora.conversationProject.v1";
  const REQUEST_PREFIX = "aurora.pendingConversationRequest.";

  function normalizeProjectId(value) {
    if (typeof value !== "string") return null;
    const normalized = value.trim();
    return normalized ? normalized : null;
  }

  function storage(storageName) {
    try {
      return root[storageName] || null;
    } catch (_) {
      return null;
    }
  }

  function readItem(store, key) {
    if (!store) return null;
    try {
      return store.getItem(key);
    } catch (_) {
      return null;
    }
  }

  function writeItem(store, key, value) {
    if (!store) return false;
    try {
      store.setItem(key, value);
      return true;
    } catch (_) {
      return false;
    }
  }

  function scopeKey(projectId) {
    return SESSION_PREFIX + encodeURIComponent(normalizeProjectId(projectId) || "__global__");
  }

  function readSession(projectId) {
    const store = storage("sessionStorage");
    const raw = readItem(store, scopeKey(projectId));
    if (raw !== null) {
      let saved;
      try {
        saved = JSON.parse(raw);
      } catch (_) {
        throw new Error("Saved Aurora conversation state is invalid; refusing to create a replacement session.");
      }
      const sessionId = saved && typeof saved.session_id === "string" ? saved.session_id.trim() : "";
      const savedProjectId = normalizeProjectId(saved && saved.project_id);
      if (!sessionId || savedProjectId !== normalizeProjectId(projectId)) {
        throw new Error("Saved Aurora conversation identity conflicts with the selected project; refusing to create a replacement session.");
      }
      return { session_id: sessionId, project_id: savedProjectId };
    }

    // Migration for a tab upgraded while an older build had an outstanding
    // request but had not yet persisted its session ID separately.
    if (normalizeProjectId(projectId) !== null || !store) return null;
    let keys = [];
    try {
      for (let index = 0; index < store.length; index += 1) {
        const key = store.key(index);
        if (typeof key === "string" && key.startsWith(REQUEST_PREFIX)) keys.push(key);
      }
    } catch (_) {
      return null;
    }
    const legacySessions = new Set();
    keys.forEach(function (key) {
      const rawRequest = readItem(store, key);
      if (!rawRequest) return;
      try {
        const request = JSON.parse(rawRequest);
        if (!request || typeof request.request_id !== "string" || !request.request_id.trim()) return;
        const remainder = key.slice(REQUEST_PREFIX.length);
        const finalSeparator = remainder.lastIndexOf(".");
        if (finalSeparator > 0) legacySessions.add(remainder.slice(0, finalSeparator));
      } catch (_) {}
    });
    if (legacySessions.size > 1) {
      throw new Error("Multiple legacy Aurora requests are pending and their session identity is ambiguous; refusing to guess or replay an order.");
    }
    if (legacySessions.size === 1) {
      const migrated = { session_id: Array.from(legacySessions)[0], project_id: null };
      if (!writeSession(migrated)) {
        throw new Error("Browser storage is unavailable; refusing to resume an order without a durable session identity.");
      }
      return migrated;
    }
    return null;
  }

  function writeSession(session) {
    if (!session || typeof session.session_id !== "string" || !session.session_id.trim()) return false;
    const projectId = normalizeProjectId(session.project_id);
    return writeItem(
      storage("sessionStorage"),
      scopeKey(projectId),
      JSON.stringify({ session_id: session.session_id.trim(), project_id: projectId })
    );
  }

  function readSelectedProject() {
    const local = storage("localStorage");
    const session = storage("sessionStorage");
    for (const store of [local, session]) {
      const raw = readItem(store, PROJECT_KEY);
      if (raw !== null) return normalizeProjectId(raw);
    }
    return null;
  }

  function writeSelectedProject(projectId) {
    const value = normalizeProjectId(projectId) || "";
    const localWritten = writeItem(storage("localStorage"), PROJECT_KEY, value);
    // Mirror the preference in session storage, but only report persistent
    // success when localStorage actually accepted it. Otherwise a stale
    // cross-reload project choice could silently override the new selection.
    writeItem(storage("sessionStorage"), PROJECT_KEY, value);
    return localWritten;
  }

  function requestFingerprint(sessionId, content) {
    const source = sessionId + "\\u0000" + content;
    let hash = 2166136261;
    for (let index = 0; index < source.length; index += 1) {
      hash ^= source.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(16) + "-" + source.length;
  }

  function requestStorageKey(sessionId, content) {
    return REQUEST_PREFIX + sessionId + "." + requestFingerprint(sessionId, content);
  }

  function getOrCreateRequestId(sessionId, content) {
    if (typeof sessionId !== "string" || !sessionId.trim()) {
      throw new Error("A persisted conversation session is required before creating a request ID.");
    }
    const store = storage("sessionStorage");
    const key = requestStorageKey(sessionId, content);
    const fingerprint = requestFingerprint(sessionId, content);
    const raw = readItem(store, key);
    if (raw !== null) {
      try {
        const saved = JSON.parse(raw);
        if (saved && saved.fingerprint === fingerprint &&
            typeof saved.request_id === "string" && saved.request_id.trim()) {
          return saved.request_id;
        }
      } catch (_) {}
    }
    let requestId;
    try {
      requestId = "request-" + (
        root.crypto && typeof root.crypto.randomUUID === "function"
          ? root.crypto.randomUUID()
          : Date.now().toString(36) + "-" + Math.random().toString(36).slice(2)
      );
    } catch (_) {
      requestId = "request-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
    }
    if (!writeItem(store, key, JSON.stringify({ fingerprint: fingerprint, request_id: requestId }))) {
      throw new Error("Browser session storage is unavailable; refusing to submit a conversation order that cannot be safely resumed.");
    }
    return requestId;
  }

  function findPendingRequestId(sessionId, content) {
    const store = storage("sessionStorage");
    const key = requestStorageKey(sessionId, content);
    const raw = readItem(store, key);
    if (raw === null) return null;
    try {
      const saved = JSON.parse(raw);
      if (saved && saved.fingerprint === requestFingerprint(sessionId, content) &&
          typeof saved.request_id === "string" && saved.request_id.trim()) {
        return saved.request_id;
      }
    } catch (_) {}
    return null;
  }

  function clearStableRequestId(sessionId, content, requestId) {
    const store = storage("sessionStorage");
    const key = requestStorageKey(sessionId, content);
    const existing = findPendingRequestId(sessionId, content);
    if (!existing || existing !== requestId || !store) return false;
    try {
      store.removeItem(key);
      return true;
    } catch (_) {
      return false;
    }
  }

  root.AuroraConversationPersistence = {
    readSession: readSession,
    writeSession: writeSession,
    readSelectedProject: readSelectedProject,
    writeSelectedProject: writeSelectedProject,
    getOrCreateRequestId: getOrCreateRequestId,
    findPendingRequestId: findPendingRequestId,
    clearStableRequestId: clearStableRequestId
  };
})(window);
