# Egress proxy enforcement

Aurora networked tools use a policy-enforcing HTTPS CONNECT proxy. The proxy accepts only CONNECT requests to an exact hostname allowlist and TCP/443.

The workload runs in a private bubblewrap network namespace connected through rootless slirp4netns with host-loopback disabled and DNS forwarding disabled. A supervisor-owned route fence is installed during sandbox bootstrap while CAP_NET_ADMIN is present. The fence removes the default route and the slirp-connected subnet route, then installs a single host route to the dedicated proxy gateway `10.0.2.2/32` on `tap0`.

After the dataplane is ready and the route fence is verified, the bootstrap drops all workload capabilities with setpriv and executes the requested command. A seccomp filter also blocks clone(), clone3(), unshare(), and setns() for the workload so it cannot create or join namespaces to regain network-control capabilities.

The resulting dataplane is:

    workload
       |
       | only reachable IPv4 destination
       v
    10.0.2.2:3128
       |
       v
    policy proxy
       |
       v
    approved HTTPS destinations

There is no default route and no direct Internet fallback inside the workload namespace. The proxy itself performs hostname allowlisting, rejects IP-literal destinations, and only permits TCP/443 CONNECT. IPv6 default routing is removed during bootstrap.

If any prerequisite, route operation, proxy setup, or sandbox hardening step fails, the networked workload is not started.

### Trust boundary

The supervisor/bootstrap phase is the only phase allowed to hold CAP_NET_ADMIN. User code starts only after capabilities are dropped. Bubblewrap provides the namespace/filesystem boundary; slirp4netns provides rootless transport; the route fence provides dataplane-level egress enforcement; the proxy provides destination policy.
