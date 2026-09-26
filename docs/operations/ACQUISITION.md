# Aurora Acquisition Service

Aurora can acquire an approved capability without giving agents arbitrary shell access.

## Implemented paths

- `ubuntu-apt`: non-interactive APT installation for registered system/runtime tools.
- `pypi`: isolated `pip --target` installation into an explicit runtime directory.
- Existing executables/distributions are detected and verified against requested versions, making acquisition idempotent.
- Networked acquisition sources are mapped to the Internet Gateway policy before installation.
- PyPI acquisition is pinned to the approved HTTPS simple index and runs non-interactively.
- Every successful or already-available acquisition can emit an audit record containing source, status, path and executable hash when a file hash is available.\n- Acquisition lifecycle is recorded in an append-only transaction journal with deterministic operation identity. Interrupted operations enter `recovery_required` instead of being silently retried.

## Internet Gateway boundary

When an `InternetGateway` is supplied, acquisition performs a deny-by-default authorization check for the network endpoints associated with the selected source.

Current source mappings:

- `pypi` → `https://pypi.org/simple/`
- `ubuntu-apt` → Ubuntu archive/security endpoints

The Gateway re-checks redirect destinations for its own requests. The acquisition subprocess itself is still the host package manager/pip; the current integration therefore provides **application-level source authorization**, not an OS-level transparent network firewall. OS-level enforcement remains a later hardening step.

## Safety boundary

The service only accepts registered `ToolSpec` objects and an allowlisted source. It invokes fixed installer argument shapes; agents cannot supply arbitrary shell strings. APT acquisition requires Linux/WSL and uses the host package manager. PyPI acquisition requires an explicit isolated target and an approved index.

`npm` and `github-release` remain registry sources but are intentionally not executable acquisition paths yet; they require their own locked-down installers and provenance/verification rules before activation.

## Transaction and recovery model\n\nEach acquisition has a deterministic operation key derived from tool, source, requested version/artifact identity, and install target. The transaction journal records `running`, `committed`, `failed`, `rolled_back`, or `recovery_required` states. A retry of a committed operation first verifies its postcondition and does not reinstall it. An interrupted operation is blocked until `recover()` can verify that the requested postcondition already exists; otherwise Aurora requires explicit cleanup rather than guessing how to repair a partially modified environment.\n\nThis is intentionally conservative: APT and pip remain external package managers, so the journal does not pretend their host-side mutations are magically atomic. The next sandbox/OS-enforcement layer can add stronger isolation around those installers.\n\n## Next hardening

1. Add package/version constraints and lock/verification metadata.
2. Add filesystem snapshots/atomic staging for package-manager mutations where practical.
3. Add OS-level network enforcement/proxying for acquisition subprocesses.
4. Add sandboxed execution of acquired tools.
