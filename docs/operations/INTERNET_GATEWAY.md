# Aurora Internet Gateway

Aurora's Internet Gateway is the network policy boundary used by acquisition and future browser/research tools.

## Contract

- Default deny.
- HTTPS only unless policy explicitly permits another protocol.
- Exact hostname allowlist.
- URL credentials are rejected.
- Redirects are re-authorized against the same policy.
- Network failures are surfaced as policy-scoped errors.
- The gateway does not execute downloaded content.

## Security boundary

The gateway is not a general proxy and does not grant arbitrary network access. Callers must use the gateway for controlled outbound requests; OS-level firewall/container enforcement remains a separate defense-in-depth layer.

## Next integration

Acquisition installers will be routed through the gateway or an OS/container network policy so that package downloads cannot silently bypass Aurora's approved destinations.
