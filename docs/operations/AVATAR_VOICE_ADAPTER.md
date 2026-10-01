# Block 12D — Avatar Voice Adapter

**Status:** IMPLEMENTED / VALIDATION PENDING  
**Branch:** `dashboard/avatar-voice-adapter-v1`  
**Base:** `main` at `14566668d6523e906f2c99b917bd2141909e2897`

## Scope

12D adds a replaceable browser-side voice presentation adapter around the existing central Director portrait slot.

It does not create a second conversation system and does not call Gemini or any model provider directly.

## Contract

The adapter exposes deterministic presentation states:

```
IDLE
LISTENING
THINKING
SPEAKING
ERROR
DISCONNECTED
```

It provides:

- speech input through feature-detected Web Speech recognition;
- explicit microphone activation from a user control;
- final/interim transcript callbacks;
- speech output through browser `speechSynthesis`;
- stop/cancel controls;
- capability detection;
- reduced-motion compatible visual state changes;
- static/text fallback when voice is unavailable;
- provider failure isolation.

Browser speech synthesis is broadly available, while speech recognition has more limited browser availability, so recognition is never treated as mandatory. The browser also requires explicit permission for microphone capture and secure-context handling for `getUserMedia`.

## Boundary

```
User action
   -> AvatarVoiceAdapter
       -> BrowserSpeechProvider
       -> transcript callback
       -> Block 12E: Aurora Conversation API
```

and:

```
Director response
   -> Block 12E conversation boundary
   -> AvatarVoiceAdapter.speak(response_text)
   -> browser speech synthesis
   -> state transitions
```

12D deliberately stops before submitting transcribed text to the Director. That connection belongs to 12E so that the conversation service remains the single authoritative user/Director boundary.

## Security and privacy

- Microphone activation occurs only from an explicit user action.
- Raw microphone streams are released immediately after recognition ends or fails.
- No audio is persisted by this adapter.
- The adapter contains no provider credentials.
- Recognition or synthesis failures fall back to text.
- The dashboard can remain operational when voice services are unavailable.

## Acceptance

12D is complete when CI proves the static dashboard contract and the adapter tests pass. Full conversational voice E2E remains a 12E/Block 12 integration proof.
