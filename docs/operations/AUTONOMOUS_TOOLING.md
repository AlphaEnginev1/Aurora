# Autonomous Tooling

Aurora detects missing capabilities, resolves approved sources, installs non-interactively, verifies the result, and exposes it only after verification.

Acquisition classes: APT/system packages, Python/PyPI, NPM, Git repositories/releases.

Required properties: idempotency, bounded retries, caching, provenance, audit events, verification and restart recovery.

Normal operation should not require a human to manually install a tool. Human approval is reserved for policy changes, sensitive capabilities, new trust domains or gated live operations.
