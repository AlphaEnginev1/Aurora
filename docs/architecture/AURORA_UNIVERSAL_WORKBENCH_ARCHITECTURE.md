# Aurora Universal Workbench & Integration Plane

**Status: ARCHITECTURE FROZEN — DESIGN BASELINE**  
**Canonical branch: `main`**  
**Design branch: `architecture/universal-workbench-contract-v1`**

## 1. Purpose

Aurora is not considered a complete autonomous work system merely because its internal orchestration, planning, model routing, research and recovery systems work.

The Director and every specialist agent must be able to perform the real work required by a mission without artificial capability bottlenecks caused by the agent's role.

This document freezes the architectural contract for that requirement.

**No implementation is authorized by this document.** Implementation must be opened as a separately scoped sequence and must preserve all previously closed acceptance boundaries.

## 2. Universal Work / Cowork principle

Aurora adopts a **Universal Work / Cowork operating model**.

The Director and all agents share the same governed work-capability plane. Specialization determines mission responsibility, expertise, authority and quality criteria; it does not permanently remove basic work capabilities.

Therefore, any Director or agent may request the capability required by its mission to:

- inspect and modify project files;
- open, read, write, convert and analyze documents;
- inspect images, screenshots, audio, video and datasets;
- browse the web, search, navigate pages, interact with forms and download/upload artifacts;
- research external sources and reconcile evidence;
- inspect and work with Git repositories and GitHub resources;
- use connected applications and external services;
- execute code and developer tooling inside governed environments;
- create, compare, transform, validate and publish artifacts;
- discover, acquire, validate and use a missing capability when policy permits.

**Universal capability does not mean unrestricted authority.** Read, write, execute, publish and destructive actions remain governed by identity, scope, policy, approval and audit.

## 3. Core architecture

The architecture is a single **Aurora Universal Workbench & Integration Plane** between missions/agents and the external world.

```
Human
  |
  v
Director
  |
  v
Mission / WorkSession
  |
  v
Capability Resolver + Policy + Identity
  |
  +--> Local Computer Gateway
  +--> File / Artifact Intake
  +--> Universal Browser
  +--> Desktop Application Gateway
  +--> Git / GitHub Connector
  +--> External Connector Registry
  +--> MCP Bridge
  +--> Research / Internet Access
  +--> Multimodal Processing
  +--> Code / Tool Execution
  +--> Capability Discovery & Acquisition
  |
  v
Artifacts + Evidence + Audit + Durable State
```

No individual connector becomes the architecture itself. MCP is an integration mechanism; GitHub is one connector; the browser is one capability family; local computer access is another.

## 4. WorkSession

Every externally oriented mission receives a durable `WorkSession` context containing at minimum:

- `work_id`
- `mission_id`
- execution environment(s): local, cloud or hybrid
- workspace roots and mount modes
- connected resources
- capability grants and policy scope
- identity / credential references
- tool and connector health
- artifact manifest
- provenance and hashes
- execution receipts
- continuity / failover state
- approval state
- correlation identifiers

Local and cloud execution must be able to continue the same durable mission without losing authoritative state.

## 5. Local Computer Gateway

Aurora must support governed access to operator-selected local resources.

Required capabilities:

- discover and inspect approved folders/files;
- read and write approved paths;
- open/import/export supported artifacts;
- create temporary project mounts;
- watch and reconcile file changes;
- preserve file hashes, versions and provenance;
- execute governed commands in the approved workspace;
- expose desktop/browser interaction through a policy boundary where required.

Full-disk access is not required. The principle is **maximum useful access within explicit scope**, not hidden unrestricted host control.

## 6. Universal File / Artifact Plane

Aurora must accept and process common work artifacts, including at minimum:

PDF, DOCX, XLSX, CSV, TXT, JSON, XML, HTML, Markdown, ZIP, source code, logs, images, screenshots, audio, video and datasets.

Every artifact must have:

- origin/source;
- project and mission association;
- type and parser;
- content hash;
- version;
- provenance;
- sensitivity classification;
- processing status;
- derived-artifact links;
- evidence/citation links where applicable.

Artifact processing must be durable and reproducible.

## 7. Universal Browser

The browser capability must support governed:

- search;
- navigation;
- tabs/windows;
- scrolling;
- click/type/select;
- forms;
- download/upload;
- screenshots;
- structured extraction;
- session/cookie management;
- authenticated workflows through credential references.

Browser actions remain sandboxed and policy-controlled.

## 8. Desktop Application Gateway

Where a task genuinely requires a desktop application rather than a browser or file API, Aurora must be able to use a governed desktop interaction layer.

Required abstraction:

- launch/open approved applications;
- inspect visible state;
- read accessible application content;
- click/type/copy/paste;
- import/export files;
- recover from application failures;
- preserve action/evidence receipts.

A desktop failure must not corrupt the mission state.

## 9. GitHub and repository integration

GitHub must be a first-class connector, not an unsupported special case.

The connector contract must cover, according to granted scope:

- repositories and metadata;
- files/directories;
- branches/tags/commits;
- issues;
- pull requests and reviews;
- Actions/workflows/runs/jobs/artifacts;
- releases;
- security/dependency signals where permission exists;
- code search;
- branch creation and controlled writes;
- PR creation and governed merge actions.

Required granular scopes should distinguish read access from write/merge actions.

Aurora must be able to connect a local workspace and a remote repository into one project source graph.

## 10. External Connector Registry

Aurora must have a connector registry for external services, initially designed to support families such as:

- GitHub;
- cloud file stores;
- email;
- chat/collaboration;
- project-management systems;
- documentation systems;
- databases;
- arbitrary governed APIs.

Each connector must expose:

- identity;
- capabilities;
- account/resource scope;
- auth method;
- health;
- permission scope;
- rate/usage limits;
- read/write/destructive classification;
- provenance and audit hooks.

OAuth/token material must never enter model context as raw secrets.

## 11. MCP Bridge

MCP is supported as a bridge for external capabilities where appropriate.

The internal Aurora capability model remains provider/connector neutral. An MCP server is registered as a capability source and passed through the same:

**discover -> validate -> authorize -> execute -> observe -> audit -> learn**

lifecycle as other tools and connectors.

Aurora must not depend on MCP availability for capabilities that can be provided through native internal adapters.

## 12. Capability discovery, acquisition and fallback

If a mission requires a capability that is unavailable:

1. the Director asks the Capability Resolver;
2. available equivalent capabilities are evaluated;
3. governed discovery is attempted;
4. acquisition/install is attempted when policy permits;
5. the capability is validated and health-checked;
6. it is registered and made available to the mission;
7. execution continues using the selected capability.

If a capability fails during work, Aurora must support capability-level fallback independently from Director-provider failover.

No agent may invent a missing capability or claim execution that did not occur.

## 13. Multimodal work

Aurora must route work to the appropriate modality:

- text/code;
- vision/image/screenshot;
- document parsing;
- audio/speech;
- video;
- structured data/spreadsheets.

The Director chooses outcomes and required competencies. The Workbench resolves the concrete capability/model/tool path.

## 14. Project intake and source graph

Aurora must be able to start a project from one or more sources:

- local folder;
- Git repository/GitHub;
- ZIP/archive;
- uploaded artifacts;
- connected cloud storage;
- external project/document systems.

Project intake must discover and register, where available:

- project identity;
- repository/workspace;
- architecture and documentation;
- languages/frameworks;
- dependencies;
- tests and CI;
- branches/PRs/issues;
- important configuration;
- artifacts and external sources.

All sources become part of an authoritative project source graph with provenance and freshness.

## 15. Capability census and mission preflight

Before starting a mission that requires external work, Aurora must be able to produce a capability census:

- available;
- connected;
- authorized;
- healthy;
- degraded;
- unavailable;
- acquirable;
- blocked by policy;
- fallback available.

The Director must know the real capability state before making execution claims.

This is a mandatory anti-bottleneck mechanism.

## 16. Security and authority model

Universal capability is constrained by:

- identity;
- project scope;
- mission scope;
- connector/resource scope;
- data classification;
- secret isolation;
- least privilege;
- explicit approval boundaries;
- sandboxing;
- network policy;
- destructive-action policy;
- auditability.

Broad read capability may be granted more freely than write/execute/publish/destructive capability.

All external side effects require an attributable actor, capability, target, authorization context and evidence receipt.

## 17. Evidence, provenance and audit

For every material external action Aurora must be able to reconstruct:

**agent -> mission -> capability -> connector/account -> target resource -> requested action -> actual execution -> result -> artifact/evidence -> authorization**

Artifacts and evidence must be durable, hashable and linked to the originating work.

## 18. Natural Director conversation

The human-facing Director conversation is a separate layer from internal planning/execution protocols.

It must remain:

- natural;
- useful;
- proactive where appropriate;
- honest about uncertainty;
- explicit about what is and is not actually executed;
- capable of explaining blockers and available alternatives.

Internal structured contracts may remain JSON/typed, but the human should experience one coherent Director.

## 19. Required final acceptance

The universal workbench is not accepted by unit tests alone.

The final acceptance mission must demonstrate, in one governed end-to-end flow:

1. local project/artifact intake;
2. GitHub repository access;
3. web research;
4. document/image analysis;
5. multiple specialist agents using shared work capabilities;
6. capability discovery or fallback;
7. code/file modification in an isolated workspace;
8. validation/tests;
9. Git branch/commit/PR workflow;
10. durable artifacts, evidence and audit;
11. restart/reconciliation without loss of mission state;
12. natural Director reporting of the real outcome.

The acceptance must prove that an agent is not blocked merely because the required work capability belongs to another historical “department”.

## 20. Architectural boundaries

This contract does not reopen:

- Increment 11;
- Increment 12;
- the frozen production-readiness queue;
- previously accepted research, routing, learning, decision, recovery or governance work.

The Universal Workbench is a new architectural completion direction built on top of those closed foundations.

It also does not authorize live production execution automatically.

## 21. Implementation rule

Before implementation begins, every capability family above must be mapped to:

- contract;
- owner/module boundary;
- policy boundary;
- durable state;
- tests;
- runtime integration;
- external/live gate where applicable;
- failure and fallback behavior;
- evidence requirements.

No implementation increment is considered complete until the corresponding acceptance evidence exists on canonical `main`.
