# Egress proxy enforcement

Aurora networked tools use a policy-enforcing HTTPS CONNECT proxy. The proxy accepts only CONNECT requests to an exact hostname allowlist and TCP/443.

The workload runs in a private bubblewrap network namespace connected through rootless slirp4netns with host-loopback disabled and DNS forwarding disabled. Bubblewrap's outer launcher is not the namespace target: when --unshare-net and --unshare-pid are enabled, Aurora reads the child-pid status emitted by --json-status-fd and passes that child PID to slirp4netns.

slirp4netns is started with --configure, --disable-host-loopback, --disable-dns, a readiness pipe (--ready-fd), and a lifecycle pipe (--exit-fd). Aurora does not release the bootstrap process until slirp4netns reports readiness.

The bootstrap process initially retains CAP_NET_ADMIN only long enough to wait for tap0, apply the route fence, and install the native nftables policy. It then execs setpriv to drop the workload capability sets. A second stage in the same injected read-only bootstrap then sets no_new_privs, installs the namespace-restriction seccomp filter, and finally execs the requested workload.

The seccomp policy does not block ordinary fork/thread creation. It blocks unshare() and setns() outright, rejects clone3(), and rejects clone() only when namespace-creation flags are present. This prevents namespace escape attempts without making normal multithreaded tools fail simply because they create processes or threads.

After the dataplane is ready, the supervisor-owned route fence removes the default route and the slirp-connected subnet route, then installs a single host route to the dedicated proxy gateway 10.0.2.2/32 on tap0.

The firewall is installed before workload privileges are dropped. It uses a native nftables base chain with policy drop, permitting only loopback, the dedicated proxy endpoint 10.0.2.2:3128, and established/related traffic. The workload cannot rely on a direct route or direct destination even if proxy environment variables are removed.

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

If any prerequisite, route operation, proxy setup, capability drop, seccomp installation, or sandbox hardening step fails, the networked workload is not started.

### Trust boundary

Bubblewrap establishes the mount, PID, UTS, IPC, cgroup, user, and network namespaces. slirp4netns provides rootless transport without host-loopback access. The bootstrap is injected read-only by Bubblewrap and is the only process allowed to perform the privileged network setup. setpriv removes workload capability inheritance/bounding and enables no_new_privs. The post-drop seccomp filter blocks namespace-changing syscalls. The route fence provides dataplane-level egress enforcement; nftables provides port-level enforcement; and the proxy provides destination-level hostname policy.
