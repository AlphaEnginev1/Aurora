# Aurora Project Registry

The Project Registry is the authoritative catalog of Aurora-managed projects.

It is intentionally separate from the Organization Registry:
- Organization Registry answers **who can work**.
- Project Registry answers **what Aurora-managed projects exist and their canonical state**.

Each project records canonical metadata:
- project identity, name and objective
- lifecycle status
- repository/workspace references
- architecture reference
- active agent roles
- active skills
- known model assignments
- task/decision indexes
- current state and timestamps

The registry validates project references against the organization, skill and inference catalogs when those catalogs are supplied.

The initial checked-in catalog is intentionally empty. A project must be added explicitly as canonical; the registry must never infer a project from model output, dashboard state, or a transient mission.

The Project Registry is infrastructure for the later Aurora Context Plane and Director Bootstrap. It does not yet replace MissionMemory or the Event Bus, and it does not yet claim to be the full project runtime state store.
