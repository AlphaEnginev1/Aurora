# Aurora execution sandbox

Aurora executes acquired tools only through a bounded sandbox. The sandbox is a trust boundary between an agent and the host operating system.

## Production backend

Linux production execution requires bubblewrap (bwrap). If it is unavailable, execution fails closed; Aurora does not silently fall back to an unrestricted subprocess.

The sandbox executes without a shell; uses a dedicated Aurora workspace as the only writable persistent filesystem; mounts system paths read-only; creates isolated PID, UTS, IPC, cgroup and network namespaces; gives the process an ephemeral /tmp; keeps only an explicit environment allowlist; sets CPU, address-space, process-count and open-file limits; bounds captured stdout/stderr; kills the process group on timeout; and never exposes API keys through inherited environment variables by default.

## Network rule

Direct network access is disabled in the sandbox. A request that enables network access is rejected. The later network-isolation block will add a controlled broker/proxy path so tools can obtain approved Internet access without bypassing Aurora's Internet Gateway.

## Filesystem rule

The sandbox is intentionally narrower than the build environment. Host package databases, credentials, SSH material and arbitrary host directories are not exposed as writable mounts. Aurora-managed workspace data is the persistent execution surface.

## Recovery interaction

Crash recovery remains responsible for acquisition state and managed installation trees. Sandbox execution does not mutate the acquisition transaction journal directly. Tool execution artifacts remain in the managed workspace and are subject to the audit/reconciliation layer.

## Security posture

This is a fail-closed boundary, not a claim of a perfect kernel-level security proof. The runtime should use a current Linux kernel and keep bubblewrap patched. Untrusted code must not be executed directly on a long-lived self-hosted runner with secrets; GitHub also warns that self-hosted runners do not provide the clean ephemeral isolation of GitHub-hosted runners.
