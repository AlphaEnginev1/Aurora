# Aurora Dashboard UI

Block 10 implements the first usable Aurora Operations Center frontend as a dependency-free static interface.

## Runtime contract

The UI consumes the read-only Dashboard Gateway through HTTP-shaped read endpoints:

- \`GET /api/dashboard/snapshot\`
- \`GET /api/dashboard/projects/{project_id}\`
- \`GET /api/dashboard/agents/{role}\`

The default base path is \`/api/dashboard\`. Deployments may set
\`window.AURORA_DASHBOARD_API_BASE\` before loading \`app.js\`.

The browser never talks directly to SQLite, task stores, providers or model adapters.

## Operations Center

The first screen contains:

- Director Core with a central portrait slot
- orbital agent workspace around the shared mailbox
- project cockpit
- recent mailbox activity
- provider/model intelligence
- skills and learning
- runtime/recovery summary

Agent and project nodes open read-only inspectors backed by the Dashboard Gateway.

## Portrait boundary

The portrait is a visual slot only in this block. Voice, speech and animation remain behind the Avatar Adapter block.

The current UI intentionally falls back to a static portrait placeholder until the canonical Aurora portrait asset and avatar adapter are connected.

## Live synchronization

The browser performs an immediate authoritative snapshot read, then refreshes the snapshot every 10 seconds while the page is visible. A successful response replaces the displayed read model as a full reconciliation; the snapshot carries the gateway event journal projection (`runtime.latest_events`) and its event IDs for ordering/inspection. This avoids treating browser memory as durable state or applying speculative mutations.

On request failure or invalid snapshot shape, the UI marks system status unknown and retries with bounded exponential backoff (up to 120 seconds). Returning to a visible tab triggers an immediate refresh. Manual refresh remains available. Requests are single-flight to prevent overlapping snapshots. The UI does not claim READY from transport connectivity; readiness comes from the authoritative Director snapshot.

The gateway transport must expose `GET /api/dashboard/snapshot` and return the existing DashboardGateway snapshot contract. This block uses polling/reconciliation, not WebSocket or SSE; those transports are not required for correctness. Avatar voice/animation remains Block 12.

## Deployment

The frontend has no build step and no third-party runtime dependency. It can be served by the eventual Dashboard API process or any static-file server.
