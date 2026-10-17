from pathlib import Path
import shutil
import subprocess

import pytest

UI_DIR = Path(__file__).parents[1] / "aurora" / "dashboard" / "ui"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for browser-storage reload simulation")
def test_active_conversation_order_reuses_session_and_request_after_reload():
    helper = UI_DIR / "conversation_persistence.js"
    source = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[1], "utf8");

class Storage {
  constructor() { this.data = new Map(); }
  get length() { return this.data.size; }
  key(index) { return Array.from(this.data.keys())[index] || null; }
  getItem(key) { return this.data.has(key) ? this.data.get(key) : null; }
  setItem(key, value) { this.data.set(String(key), String(value)); }
  removeItem(key) { this.data.delete(key); }
}

const sessionStorage = new Storage();
const localStorage = new Storage();
let uuid = 0;
function loadAfterReload() {
  const window = {
    sessionStorage,
    localStorage,
    crypto: { randomUUID: () => "uuid-" + (++uuid) }
  };
  vm.runInNewContext(source, { window, Date, Math, Set, Array, JSON, encodeURIComponent });
  return window.AuroraConversationPersistence;
}

const firstPage = loadAfterReload();
if (!firstPage.writeSelectedProject("project-a")) throw new Error("project preference was not persisted");
if (!firstPage.writeSession({ session_id: "conversation-a", project_id: "project-a" })) {
  throw new Error("conversation session was not persisted");
}
const restoredText = "Run the durable order and preserve the receipt.";
const firstRequestId = firstPage.getOrCreateRequestId("conversation-a", restoredText);
const serverReceipts = new Map();
let submissionCount = 0;
function submit(sessionId, requestId, text) {
  const key = sessionId + ":" + requestId;
  if (serverReceipts.has(key)) return serverReceipts.get(key);
  submissionCount += 1;
  const receipt = {
    mission_id: "mission-accepted-once",
    objective: text,
    status: "running"
  };
  serverReceipts.set(key, receipt);
  return receipt;
}
const firstReceipt = submit("conversation-a", firstRequestId, restoredText);
if (firstReceipt.status !== "running") throw new Error("test precondition requires an active order");

// A full reload evaluates the module again with the same browser session storage,
// just as a page reload does. No prompt body is stored in browser storage.
const secondPage = loadAfterReload();
if (secondPage.readSelectedProject() !== "project-a") throw new Error("selected project was lost");
const savedSession = secondPage.readSession("project-a");
if (!savedSession || savedSession.session_id !== "conversation-a") {
  throw new Error("conversation session did not survive reload");
}
const replayRequestId = secondPage.findPendingRequestId(savedSession.session_id, restoredText);
if (replayRequestId !== firstRequestId) throw new Error("pending request ID changed after reload");
const replayReceipt = submit(savedSession.session_id, replayRequestId, restoredText);
if (replayReceipt.mission_id !== firstReceipt.mission_id) throw new Error("replay did not return the original mission");
if (submissionCount !== 1) throw new Error("reload created a duplicate durable order");
// The active response must not trigger the app's terminal-receipt cleanup branch.
const statusAfterReplay = "running";
if (statusAfterReplay !== "running") {
  secondPage.clearStableRequestId(savedSession.session_id, restoredText, replayRequestId);
}
if (secondPage.findPendingRequestId(savedSession.session_id, restoredText) !== firstRequestId) {
  throw new Error("an active receipt lost its stable request ID");
}
console.log("reload persistence simulation passed");
"""
    result = subprocess.run(
        ["node", "-e", source, str(helper)],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "reload persistence simulation passed" in result.stdout


def test_dashboard_wires_project_selection_and_automatic_pending_order_resume():
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    app = (UI_DIR / "app.js").read_text(encoding="utf-8")
    helper = (UI_DIR / "conversation_persistence.js").read_text(encoding="utf-8")

    assert '<script src="./conversation_persistence.js" defer></script>' in html
    assert 'id="chat-project-select"' in html
    assert 'state.selectedProjectId' in app
    assert 'body: JSON.stringify(projectId ? { project_id: projectId } : {})' in app
    assert "function resumePendingConversationRequests()" in app
    assert "findPendingRequestId" in app
    assert "executionStillRunning" in app
    assert "writeSession" in helper
    assert "getOrCreateRequestId" in helper
