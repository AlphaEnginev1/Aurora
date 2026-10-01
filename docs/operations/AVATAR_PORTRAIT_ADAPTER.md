# Block 12F — Canonical Aurora Portrait + Visual State Adapter

**Status:** IMPLEMENTED / VALIDATION PENDING  
**Branch:** `dashboard/avatar-portrait-adapter-v1`  
**Base:** `main` at `909962a513f5c58484d9a20374d2a738432cbe01`

## Purpose

Make the central Aurora portrait a real presentation asset while keeping the asset replaceable and independent from the Director conversation state.

## Portrait contract

The dashboard now provides a dedicated image element:

`#director-portrait-image`

The asset is supplied through:

`window.AURORA_AVATAR_PORTRAIT_URL`

No portrait is hard-coded into the application and no substitute image is generated when the canonical asset is unavailable.

When the configured asset loads:
- the canonical image becomes visible;
- the static placeholder is hidden.

When it fails:
- the image is hidden;
- the existing static placeholder remains visible;
- conversation and text output are unaffected.

## Visual states

The portrait reacts to the Avatar Adapter states:

```
IDLE          normal presentation
LISTENING     subtle expansion/pulse
THINKING      restrained visual processing state
SPEAKING      subtle speaking animation
ERROR         brief error indication
DISCONNECTED  muted/degraded presentation
```

Users who prefer reduced motion receive a non-animated presentation.

## Asset availability

The original portrait previously designated as Aurora's central image was not found in the repository or returned by the available file-library search. Therefore this block intentionally does not substitute another image.

The deployment must supply the original asset through `AURORA_AVATAR_PORTRAIT_URL` (or a future equivalent canonical asset resolver) before the portrait acceptance gate can be considered complete.

## Architecture

```
Aurora conversation
       |
       v
AvatarVoiceAdapter
       |
       +--> state
       |
       +--> portrait presentation
       |
       +--> optional browser speech
```

The portrait owns no conversation history, inference state, task state, or Aurora persistence.

## Acceptance status

The code and contract tests are implemented. Final CI validation is required before merging. Canonical asset presence remains an external acceptance dependency.
