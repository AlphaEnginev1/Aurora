# Block 12E — Dashboard ↔ Director Conversation API

**Status:** IMPLEMENTED / VALIDATION PENDING  
**Branch:** `dashboard/avatar-voice-adapter-v1`  
**Base:** `main` at `14566668d6523e906f2c99b917bd2141909e2897`

## Purpose

Connect the dashboard's text and voice input to the Aurora-owned `DirectorConversationService`.

The existing Dashboard Gateway remains read-only. Conversation commands use a separate command boundary.

## Runtime path

```
Dashboard text / voice transcript
        |
        v
DashboardConversationAPI
        |
        v
DirectorConversationService
        |
        +--> DirectorBootstrap / ContextGate
        |
        +--> ModelRouter / ProviderRegistry
        |
        v
Director response
        |
        +--> dashboard text
        +--> AvatarVoiceAdapter.speak()
```

## HTTP contract

`create_dashboard_server()` provides:

- `GET /api/dashboard/snapshot`
- `GET /api/dashboard/projects/{project_id}`
- `GET /api/dashboard/agents/{role}`
- `POST /api/conversation/session`
- `POST /api/conversation/{session_id}/turn`
- `GET /api/conversation/{session_id}/turns`

Conversation requests use JSON and return the durable session/turn identity together with response text, model/provider identity, execution identity, context manifest and presentation hints.

## Idempotency and durable execution status

Turns that can create a durable mission must include a stable `request_id`. Keep the same ID after a lost HTTP response, wait timeout, network interruption or browser retry. Aurora binds the ID to the exact input and rejects reusing it with different input. Missing IDs are rejected before mission acceptance, including when a provider unexpectedly requests `run_mission`.

The top-level `state` describes the saved conversation turn. The separate `execution_status` describes its durable mission receipt. The conversation turn may be complete while the mission is still running. The browser must retain the stable request ID in that case; replaying the same ID refreshes the durable receipt from the runtime journal without rerunning the order.

## Browser behavior

The Operations Center now:

- creates a conversation session;
- accepts typed Director messages;
- sends final voice transcripts to the same conversation endpoint;
- displays the returned Director response;
- invokes the replaceable speech adapter when output speech is available;
- keeps readable text as the fallback;
- marks the avatar disconnected when the conversation endpoint is unavailable.

No browser code contains provider credentials or calls Gemini/NVIDIA/Claude directly.

## Runtime startup

The Aurora daemon can serve the dashboard and conversation API with:

```
python -m aurora.orchestration.daemon --dashboard
```

The default bind is loopback on port 8787. A non-loopback bind is refused unless `AURORA_DASHBOARD_API_TOKEN` is configured.

This token protects the conversation HTTP endpoints but is not intended to be exposed to arbitrary browser clients. Remote deployment should place the dashboard behind the application's authenticated session/reverse-proxy boundary before exposing it beyond the local host.

## Governance boundary

A conversation turn does not bypass Aurora governance. The conversation service can only request inference through the existing ModelRouter/ContextGate path, and any future side-effecting Director action must remain a governed Aurora action rather than a dashboard mutation.

## Scope remaining

12E proves the text/voice transport path at the HTTP boundary. Canonical portrait asset integration and a complete interactive browser E2E with a real deployed Director provider remain later acceptance work.
