# Egress proxy enforcement

Aurora networked tools use a policy-enforcing HTTPS CONNECT proxy. The proxy accepts only CONNECT requests to an exact hostname allowlist and TCP/443.

The workload runs in a private bubblewrap network namespace connected through rootless slirp4netns with host-loopback disabled and DNS forwarding disabled. Bubblewrap's outer launcher is not the namespace target: when --unshare-net and --unshare-pid are enabled, Aurora reads the child-pid status emitted by --json-status-fd and passes that child PID to slirp4netns.

slirp4netns is started with --configure, --disable-host-loopback, --disable-dns, a readiness pipe (--ready-fd), and a lifecycle pipe (--exit-fd). Aurora does not release the workload until slirp4netns reports readiness. If slirp exits before readiness, or the readiness signal is not received within the configured timeout, networked execution fails closed.

After the dataplane is ready, a supervisor-owned route fence is installed while CAP_NET_ADMIN is present. The fence removes the default route and the slirp-connected subnet route, then installs a single host route to the dedicated proxy gateway 10.0.2.2/32 on tap0.

The firewall is installed before workload privileges are dropped. It uses a native nftables base chain with policy drop, permitting only loopback, the dedicated proxy endpoint 10.0.2.2:3128, and established/related traffic. The workload cannot rely on a direct route or direct destination even if proxy environment variables are removed.

After the dataplane is ready and the route fence/firewall are verified, the bootstrap drops all workload capabilities with setpriv and executes the requested command. A seccomp filter also blocks clone(), clone3(), unshare(), and setns() for the workload so it cannot create or join namespaces to regain network-control capabilities.

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

The supervisor/bootstrap phase is the only phase allowed to hold CAP_NET_ADMIN. User code starts only after capabilities are dropped. Bubblewrap provides the namespace/filesystem boundary; slirp4netns provides rootless transport; the route fence provides dataplane-level egress enforcement; nftables provides port-level enforcement; and the proxy provides destination-level hostname policy.
