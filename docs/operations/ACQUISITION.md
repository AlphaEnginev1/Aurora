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

## Transaction and recovery model\n\nEach acquisition has a deterministic operation key derived from tool, source, requested version/artifact identity, and install target. The transaction journal records `running`, `committed`, `failed`, `rolled_back`, or `recovery_required` states. A retry of a committed operation first verifies its postcondition and does not reinstall it. An interrupted operation is blocked until `recover()` can verify that the requested postcondition already exists; otherwise Aurora requires explicit cleanup rather than guessing how to repair a partially modified environment.\n\nThis is intentionally conservative. Aurora-managed PyPI runtime directories now use a real staging directory plus a retained pre-install filesystem snapshot. The verified staged tree is committed with same-filesystem renames; if verification or commit fails, the original target is restored. `apt` remains a host package-manager mutation and is not claimed to have filesystem rollback semantics.\n\n## Filesystem staging and rollback\n\nFor Aurora-managed PyPI runtime directories, installation never writes directly into the live target. Aurora creates a pre-install snapshot, installs into a sibling staging directory, verifies package metadata and `RECORD` there, and only then swaps the staged directory into place. The previous tree is retained during the operation so commit failures can restore it. Successful commits discard the temporary snapshot.\n\nThe implementation relies on same-filesystem rename/replace semantics for the final directory switch; Python documents `os.replace()` as atomic on POSIX when the operation succeeds and the source/destination are on the same filesystem.\n\nSnapshots are confined to Aurora-managed directories. They are not used to pretend that `/usr` or a host APT database can be rolled back.\n\n## Acquisition v2: verified offline installation

PyPI acquisition is now split into two explicit trust boundaries:

1. Aurora resolves the requested release through the `InternetGateway` and selects a trusted HTTPS wheel from `files.pythonhosted.org`.
2. The artifact is downloaded into an Aurora-managed content-addressed cache and its SHA-256 is verified against PyPI release metadata.
3. The verified wheel is copied into a temporary Aurora acquisition workspace.
4. `pip` runs inside the Bubblewrap execution sandbox with networking disabled and `--no-index`, so the installer cannot reach an external package index or bypass the gateway.
5. The installed distribution is verified from its `METADATA` and `RECORD` files before the staged filesystem is activated.
6. The transaction journal and filesystem snapshot retain the existing crash-recovery and rollback guarantees.

The E2E security battery validates the complete path against a real PyPI package: download, cryptographic verification, offline installation, filesystem activation, and post-install execution in the sandbox.

## Acquisition v3: dependency closure

When a network-enabled sandbox is supplied, PyPI acquisition can resolve a complete binary dependency closure without giving the installer direct host networking:

1. pip download runs inside the controlled Bubblewrap network sandbox with the egress proxy and an explicit PyPI index.
2. Only wheel artifacts are accepted; the resolver output is bounded by artifact count and total byte limits.
3. Aurora reads each wheel's Name/Version metadata and asks the Internet Gateway for the exact PyPI release metadata for that filename.
4. Each selected wheel must match a PyPI-provided SHA-256 before entering the content-addressed cache. Artifacts not attested by PyPI are rejected.
5. The final pip install runs in the network-disabled sandbox using only the cached wheels and --no-index.
6. Every installed distribution has its RECORD hashes verified before the staged runtime tree is activated.

The networked resolver is intentionally separate from the offline installer. This prevents a package's dependency graph from becoming an implicit network escape during activation.

## Next hardening

1. Add package/version constraints and lock/verification metadata.
2. Add stronger recovery orchestration for retained snapshots and crash-interrupted commit states.
3. Add OS-level network enforcement/proxying for acquisition subprocesses.
4. Add sandboxed execution of acquired tools.


## Crash recovery and reconciliation

Managed filesystem activation now persists a snapshot manifest before staging and records activation phases (prepared, staged, commit_pending, swap_started, activated, committed, rolled_back). The acquisition transaction journal also records commit_pending before a PyPI runtime swap.

On restart, Aurora can reconcile an interrupted managed transaction deterministically:

- target + old target + verified live postcondition -> finalize activation and remove the old tree;
- target absent + old target present -> restore the known-good previous tree;
- no original target + verified staged tree -> activate the staged tree;
- live target without staging/old tree -> finalize only when its postcondition verifies;
- ambiguous or unverified state -> refuse activation and require recovery rather than guessing.

Recovery is bounded to Aurora-managed runtime directories. Host APT mutations remain outside this filesystem reconciliation model.
