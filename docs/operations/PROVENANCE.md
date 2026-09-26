# Package Verification and Provenance

Aurora must not treat successful installation as proof that an acquired dependency is trustworthy. Acquisition records both what was requested and what artifact was actually installed.

## Verification layers

1. **Source policy** — the capability broker and Internet Gateway authorize the source.
2. **Artifact identity** — SHA-256 is recorded when the installer exposes it.
3. **Package identity** — package name and exact resolved version are recorded.
4. **Installed-file integrity** — Python distributions are checked against their `RECORD` hashes.
5. **Origin** — the download URL/index is recorded when available.
6. **Audit** — verification status and provenance become part of the acquisition event.

Python wheels are required to carry `METADATA`, `WHEEL`, and `RECORD`; `RECORD` contains hashes for installed files. Aurora uses those hashes as an installed-integrity check.

For PyPI, pip's installation report is the runtime input for the resolved artifact URL, package version, and archive hashes. When an index exposes hosted provenance/attestation metadata, Aurora may record its URL and identity for later verification.

## Trust rule

An acquisition is not marked `verified` merely because the process returned exit code zero. The desired state is:

- trusted source
- exact package/version identity
- artifact SHA-256 where available
- installed-file verification where supported
- provenance URL/attestation metadata where available

If a required verification layer fails, Aurora must fail closed rather than silently downgrade the artifact to trusted.

## Current limitations

- Ubuntu APT currently records executable SHA-256 and installed package version only when the package manager query succeeds; archive-level signatures/hashes are not yet persisted.
- PyPI provenance attestations are detected/recorded when surfaced by the index/report path; signature verification is a follow-up hardening step.
- npm and GitHub release acquisition remain registered but are not executable acquisition paths.
- OS-level network enforcement for installer subprocesses is still a separate hardening task.
