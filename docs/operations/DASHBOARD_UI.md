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

## Live boundary

Block 10 uses authoritative snapshot reads and manual refresh. It does not own an event stream or reconciliation loop.

Snapshot + incremental ordered updates + reconnect/reconciliation remain Block 11.

## Deployment

The frontend has no build step and no third-party runtime dependency. It can be served by the eventual Dashboard API process or any static-file server.
