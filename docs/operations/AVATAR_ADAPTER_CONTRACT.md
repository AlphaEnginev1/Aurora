# Block 12A — Avatar Adapter Contract and Integration Audit

**Status:** AUDIT COMPLETE / IMPLEMENTATION NOT YET STARTED  
**Canonical base:** `main` at `27f4ef96b10a5aeb598aa0827deea3daf6333263`  
**Working branch:** `dashboard/avatar-adapter-v1`

## Purpose

Define the replaceable presentation boundary for Aurora's central Director portrait without moving conversation state, identity, memory, governance, or model routing into the browser or an avatar vendor.

## Verified current state

- The dashboard has a central `#director-portrait` slot with a static placeholder.
- `app.js` loads authoritative dashboard snapshots and supports project/agent read-only inspectors.
- The Dashboard Gateway is a read-only aggregation boundary. Its current exposed surfaces are snapshot/project/agent/mailbox/domain reads.
- The frontend uses `/api/dashboard` read endpoints. The repository tree currently contains no dedicated avatar, speech, audio, or Director conversation API module.
- Dashboard synchronization is snapshot polling/reconciliation; Block 11 is not SSE/WebSocket.
- No canonical portrait image is present in the inspected dashboard UI files. Do not replace Daniel's designated portrait with generated or stock artwork.

## Architectural contract

The Avatar Adapter is a replaceable client-side presentation subsystem. It consumes typed conversation and presentation events from an Aurora-owned conversation boundary; it never owns canonical conversation history or Aurora state.

Responsibilities:
- render the canonical portrait asset when supplied, with accessible static fallback;
- present explicit states: `IDLE`, `LISTENING`, `THINKING`, `SPEAKING`, `ERROR`, `DISCONNECTED`;
- receive response text and optional speech/animation cues;
- expose user-initiated microphone and playback controls only after a separately defined permission and privacy contract;
- tolerate unavailable speech, animation, or avatar providers without interrupting Director execution;
- respect reduced-motion preference and provide text-equivalent output.

Non-responsibilities:
- Director reasoning, memory, context bootstrap, task orchestration, model/provider selection, authorization, or durable conversation storage;
- direct browser access to model-provider credentials;
- claiming that audio was heard or spoken unless the respective browser/provider operation actually succeeds.

## Required boundary (to define before wiring)

Conversation API (Aurora-owned) should eventually provide:
- session/conversation identifier;
- user message submission and Director response lifecycle;
- correlation/request identifier;
- response text;
- lifecycle events sufficient to drive avatar state;
- explicit error and cancellation outcomes.

Avatar adapter ports:
- `render(state, presentationPayload)`;
- optional `speak(text, voiceConfig)` behind a speech-provider interface;
- `stopSpeaking()`;
- `dispose()`.

The exact transport and voice provider are intentionally undecided until repository contracts and runtime hosting are audited. Do not invent a route or bind the UI directly to Gemini.

## Security, privacy, resilience

- Microphone capture must be explicit user action, browser-permission aware, visibly indicated, and stoppable.
- Do not persist raw audio by default; any transcription/audio retention needs a separate governed policy.
- Escape/render untrusted transcript and response text safely.
- Speech/avatar failure is presentation degradation, not Director failure.
- On disconnect, stop or safely settle active capture/playback and show `DISCONNECTED`; reconnect must rehydrate from Aurora-owned conversation state.
- Static portrait + readable text remains a complete fallback.
- No voice cloning or high-fidelity facial animation is required for the first increment.

## Acceptance gates for Block 12

1. Canonical portrait asset is supplied and referenced without substitution.
2. Adapter state transitions are deterministic and tested.
3. Text conversation uses an Aurora-owned, authenticated/governed API boundary.
4. Optional speech input/output is permissioned, replaceable, and failure-isolated.
5. Accessibility and reduced-motion behavior are covered.
6. Unit/contract tests and dashboard E2E prove idle → user input → Director response → speaking/presentation → settled state, plus disconnect/error fallback.
7. CI green on the PR head, then merge only after review.

## Audit conclusion

Block 12A establishes the boundary and confirms the current UI is presentation-only. The key integration gap is not portrait animation: it is the absence of a discovered, verified Director conversation interface suitable for the dashboard. Next implementation step is to inspect the Director runtime's public/session entry points and determine whether a conversation service can be exposed through a governed command API without widening the read-only Dashboard Gateway. The original portrait asset must also be supplied or located before asset integration.
