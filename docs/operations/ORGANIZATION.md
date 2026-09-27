# Aurora Organization Registry

The organization registry is the declarative authority for Aurora's internal topology.

It defines:
- departments and their mission boundaries
- agent identities and roles
- capabilities each agent may request
- preferred inference models for each agent and department

The runtime factory loads `config/organization.json` together with the inference policy.
A Director plan is rejected when it names an unknown role or requests a capability not
authorized for that role.

## Routing

Model selection remains provider-neutral. A task is resolved to an agent role; the
agent's model preferences are passed to the inference router in priority order.
Disabled or incompatible preferred models are skipped. The router falls back to an
enabled compatible model when no preferred candidate is available.

This means the organization file can define multiple preferred NVIDIA/NIM models as
the inference catalog grows without changing the orchestration code.

## Security boundary

The organization registry does not grant capability access by itself. A requested
capability must still pass the existing autonomy policy, capability broker,
acquisition/verification layer, and sandbox. Organizational authorization is an
additional deny-first layer.

## Current departments

Research, engineering, data, verification, security, operations, discovery, and
synthesis are represented in the checked-in baseline configuration. More agents can
be added declaratively only when their department, capabilities, and model routing
preferences pass registry validation.
