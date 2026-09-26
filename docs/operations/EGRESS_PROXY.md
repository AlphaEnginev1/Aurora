# Egress proxy enforcement

Aurora networked tools use a policy-enforcing HTTP CONNECT proxy. The proxy accepts only HTTPS CONNECT requests to an exact hostname allowlist and TCP/443.

The namespace remains private and is connected through rootless slirp4netns. A namespace firewall uses default-deny OUTPUT and permits only loopback, DNS to the slirp DNS gateway, and TCP to the dedicated proxy endpoint. A workload therefore cannot open a direct external TCP connection to bypass the proxy.

The enforcement sequence is: create private user/network namespace; attach slirp4netns with host-loopback disabled; start the policy proxy; install the complete default-deny OUTPUT ruleset; verify the ruleset; inject HTTP_PROXY/HTTPS_PROXY; then drop CAP_NET_ADMIN before running the workload.

If any prerequisite is missing or the firewall cannot be verified, networking remains disabled. There is no fallback to direct networking.

slirp4netns documents rootless namespaces and --disable-host-loopback. The proxy layer is necessary because transport alone does not enforce destination policy.

### E2E boundary

Networked workloads run with a private bubblewrap network namespace, rootless slirp4netns with host-loopback disabled, DNS forwarding disabled, and a bootstrap route fence that removes the default route and retains only `10.0.2.2/32` for the egress proxy. The workload then drops all Linux capabilities and cannot recreate the default route. The proxy separately enforces exact hostname allowlisting and TCP/443 CONNECT only.
