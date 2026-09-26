# Isolated Network Dataplane

Aurora keeps tool execution inside a bubblewrap network namespace (`--unshare-net`).
This phase adds a rootless `slirp4netns` transport that can attach user-mode networking
to that already-isolated namespace.

## Security boundary
- The sandbox must keep `--unshare-net`.
- `slirp4netns` is attached to the sandbox namespace; the host network namespace is never shared.
- `--disable-host-loopback` prevents the dataplane from exposing host loopback by default.
- If `slirp4netns` is unavailable, the dataplane fails closed.
- This phase does **not** yet expose the dataplane to arbitrary sandbox tools.
- This phase does **not** claim packet-level hostname/IP allowlisting.
- Direct sandbox networking therefore remains disabled in production until a policy-enforcing egress proxy is integrated.

## Why the proxy comes next
A user-mode network transport can provide connectivity, but by itself it does not enforce Aurora destination policy.
Giving arbitrary tools direct slirp connectivity would let them bypass NetworkBroker/InternetGateway.

The next integration must put a policy-enforcing egress proxy in the data path before enabling networked tools.
The proxy must enforce deny-by-default policy, validate redirects and DNS, prevent host-loopback access, audit every request, and fail closed when unavailable.

Until then, SandboxExecutor continues to reject direct network access.
