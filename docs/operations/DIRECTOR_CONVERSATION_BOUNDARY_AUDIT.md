# Block 12B — Director Runtime / Conversation Boundary Audit

**Status:** AUDIT COMPLETE  
**Canonical runtime:** `main`  
**Working branch:** `dashboard/avatar-adapter-v1`  
**Audit base:** `27f4ef96b10a5aeb598aa0827deea3daf6333263`

## Findings

### Existing authoritative Director entry points

Aurora currently exposes a durable mission path:

```
DirectorRuntime.submit_mission()
    -> DurableDirectorService.submit_mission()
    -> DirectorPlanner.plan()
    -> team/task validation
    -> DurableTaskStore.enqueue()
    -> EventBus mission_submitted
    -> DirectorRuntime.run_once()/run_forever()
```

The planner accepts:
- `objective`
- optional structured `context`
- optional `model`

The durable mission service assigns a mission identifier, persists the validated task graph, and publishes durable events.

### Existing Director bootstrap

`DirectorBootstrap.initialize()` creates an authoritative Director startup state through the Context Gate. It loads Director model policy, active decision policy/contract, decision performance and decision knowledge. Its state exposes context-backed messages intended for the first Director conversation.

### Missing capability

The repository contains **no dedicated user conversation/session API** for:
- creating or resuming a conversational session;
- submitting a user chat message and returning a Director response;
- streaming or publishing Director response lifecycle events;
- carrying a conversation/session identifier across turns;
- producing avatar presentation events from an interactive conversation;
- microphone input, transcription, speech output, or audio playback.

Repository code search found references to “conversation” only in architecture/bootstrap documentation and no concrete chat/voice runtime implementation.

## Architectural consequence

The Avatar Adapter must **not** call `DirectorRuntime.submit_mission()` as though a mission were a conversation. A user conversation can generate governed Aurora actions/missions, but the conversational session itself needs its own Aurora-owned boundary.

The Dashboard Gateway must remain read-only. A future conversation API should be a separate command/conversation boundary that delegates into Director/Context/Governance as appropriate.

## Required next boundary

Before 12C/12D UI work, Aurora needs a minimal provider-neutral conversation contract:

```
ConversationSession
    session_id
    created_at
    state
    director_context_manifest_id

ConversationTurn
    turn_id
    session_id
    user_input
    correlation_id
    created_at

DirectorResponse
    turn_id
    session_id
    response_text
    state
    correlation_id
    created_at
    presentation_hints
```

Minimum lifecycle:

```
USER_INPUT
   -> authenticated conversation command
   -> Director context/bootstrap
   -> Director model execution
   -> response persisted/audited as appropriate
   -> response event
   -> Avatar Adapter presentation
```

The contract must keep:
- conversation state in Aurora;
- provider/model selection behind existing ModelRouter/provider abstractions;
- governance on any action with side effects;
- avatar rendering outside the authoritative runtime state;
- text response available even when speech/animation fails.

## Do not implement yet

Do not bind the browser directly to:
- Gemini APIs;
- provider credentials;
- `DirectorPlanner` internals;
- SQLite;
- `DashboardGateway` mutation methods;
- ad-hoc `/chat` endpoints without a session/correlation contract.

## 12B acceptance result

**PASS — audit boundary established.**

The runtime has a solid mission/execution entry point and a validated bootstrap/context contract, but an interactive conversation boundary is still missing. This is a real architectural gap and must be implemented before the avatar can honestly be described as conversational.

## Next implementation unit

**12C — Minimal Aurora Conversation API contract and session service.**

Its scope should be limited to a provider-neutral text conversation path first. Voice, animation and the canonical portrait should remain adapters on top of that verified text path.
