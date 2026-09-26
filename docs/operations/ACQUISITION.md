# Aurora Acquisition Service

Aurora can acquire an approved capability without giving agents arbitrary shell access.

## Implemented paths

- `ubuntu-apt`: non-interactive APT installation for registered system/runtime tools.
- `pypi`: isolated `pip --target` installation into an explicit runtime directory.
- Existing executables are detected first, making acquisition idempotent.
- Every successful or already-available acquisition can emit an audit record containing source, status, path and executable hash when a file hash is available.

## Safety boundary

The service only accepts registered `ToolSpec` objects and an allowlisted source. It invokes fixed installer argument shapes; agents cannot supply arbitrary shell strings. APT acquisition requires Linux/WSL and uses the host package manager. PyPI acquisition requires an explicit isolated target.

`npm` and `github-release` remain registry sources but are intentionally not executable acquisition paths yet; they require their own locked-down installers and provenance/verification rules before activation.

## Next hardening

1. Put APT and pip behind the Internet Gateway policy.
2. Add package/version constraints and lock/verification metadata.
3. Add dry-run planning and transaction recovery.
4. Connect the broker to acquisition after authorization.
5. Add sandboxed execution of acquired tools.
