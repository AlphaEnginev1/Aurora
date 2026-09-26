# Aurora Acquisition Service

Aurora can acquire an approved capability without giving agents arbitrary shell access.

## Implemented paths

- `ubuntu-apt`: non-interactive APT installation for registered system/runtime tools.
- `pypi`: isolated `pip --target` installation into an explicit runtime directory.
- Existing executables are detected first, making acquisition idempotent.
- Networked acquisition sources are mapped to the Internet Gateway policy before installation.
- PyPI acquisition is pinned to the approved HTTPS simple index and runs non-interactively.
- Every successful or already-available acquisition can emit an audit record containing source, status, path and executable hash when a file hash is available.

## Internet Gateway boundary

When an `InternetGateway` is supplied, acquisition performs a deny-by-default authorization check for the network endpoints associated with the selected source.

Current source mappings:

- `pypi` → `https://pypi.org/simple/`
- `ubuntu-apt` → Ubuntu archive/security endpoints

The Gateway re-checks redirect destinations for its own requests. The acquisition subprocess itself is still the host package manager/pip; the current integration therefore provides **application-level source authorization**, not an OS-level transparent network firewall. OS-level enforcement remains a later hardening step.

## Safety boundary

The service only accepts registered `ToolSpec` objects and an allowlisted source. It invokes fixed installer argument shapes; agents cannot supply arbitrary shell strings. APT acquisition requires Linux/WSL and uses the host package manager. PyPI acquisition requires an explicit isolated target and an approved index.

`npm` and `github-release` remain registry sources but are intentionally not executable acquisition paths yet; they require their own locked-down installers and provenance/verification rules before activation.

## Next hardening

1. Add package/version constraints and lock/verification metadata.
2. Add dry-run planning and transaction recovery.
3. Add OS-level network enforcement/proxying for acquisition subprocesses.
4. Add sandboxed execution of acquired tools.
