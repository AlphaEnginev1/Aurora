# Aurora Dashboard Architecture — Final Blueprint v1

**Status:** ARCHITECTURE CLOSED / REFERENCE BLUEPRINT  
**Branch:** architecture/dashboard-final-v1  
**Canonical Aurora runtime:** main  
**Scope:** dashboard + the Aurora contracts required to make the dashboard truthful, live, inspectable and provider/model agnostic.

## 1. Architectural principle

The dashboard is a **control and visualization surface**, never the source of truth.

Aurora remains authoritative for:
- projects and their state
- organization, agents and departments
- skills and skill versions
- models and providers
- tasks and executions
- decisions and decision learning
- research evidence and trusted knowledge
- operational events and recovery state
- audit/provenance

The dashboard reads authoritative state and events, and sends commands only through governed Aurora control paths.

## 2. User experience

The primary screen is the Aurora Operations Center:

```
                           AGENT
                             |
                 AGENT ---- 📬 ---- AGENT
                             |
                         PROJECTS
                             |
                    👤 AURORA DIRECTOR
```

The central Director portrait is the conversational focus. It may speak and animate, but avatar rendering is an adapter behind Aurora's conversation/control API; it must not own conversational state.

Clicking an agent opens a complete Agent Inspector:
- identity / role / department
- current project(s)
- current task
- current state
- active skill and skill version
- selected model / provider
- model routing decision and candidates
- recent executions
- performance history
- verification/evidence state
- failures and recovery
- learning/evolution history
- mailbox messages relevant to the agent
- authorized capabilities

Clicking a project opens the Project Inspector:
- objective and status
- architecture / repository reference
- active agents
- tasks and dependencies
- current decisions
- evidence / knowledge
- events / activity
- model allocation
- skills
- blockers and recovery state

## 3. Dashboard modules

### A. Director Core
Conversation, current Director identity, current model/provider, context provenance, current objective, recent decisions, pending approvals, and live status.

### B. Agent Operations Center
Visual workspace of agents around the shared mailbox, live states, workload and drill-down.

### C. Project Cockpit
All Aurora projects, project health/state, dependencies, activity and entry into project detail.

### D. Shared Mailbox
Unified operational inbox backed by Aurora's durable event/memory infrastructure. Messages/events retain correlation and provenance.

### E. Model Intelligence
Provider catalog, model catalog, capabilities, health, routing, observed performance, cost/latency/quality metadata, and learning history.

### F. Skills & Learning
Skill catalog, active versions, evaluations, evidence, evolution proposals, promotion/rollback state and model specialization.

### G. Research & Knowledge
Sources, claims, evidence, trusted knowledge artifacts, citation graph, retrieval context and provenance.

### H. Runtime & Recovery
Worker state, tasks, Event Bus activity, incidents, retries, stale ownership, recovery drills, gateway/network status and observability.

### I. Audit & Governance
Decisions, policy state, authorization records, model/provider changes, skill changes and immutable evidence.

## 4. Final system architecture

```
                           HUMAN
                             |
                     Dashboard / Voice
                             |
                    Dashboard Gateway/API
                             |
                 Dashboard Read/Command Layer
                    /                     \
          Read Model / Snapshots       Command API
                 |                        |
           Context Plane              Governance
                 |                        |
        +--------+---------+         Aurora Runtime
        |        |         |             |
     Projects  Mailbox  Knowledge      Tasks/Events
        |        |         |             |
        +--------+---------+-------------+
                         |
                  Organization/Skills
                         |
                   Model / Provider
                       Registry
                         |
                    Model Router
                         |
        +----------------+----------------+
        |                |                |
      Gemini           Claude          NVIDIA
        |                |                |
        +----------------+----------------+
                         |
                    Execution
                         |
                 Evaluation/Evidence
                         |
                 Learning / Performance
                         |
                    Context Plane
                         |
                    next cycle
```

## 5. Aurora Context Plane

The Context Plane is mandatory infrastructure for the Director and for every project model.

### Authoritative context domains
- Aurora identity and governance
- Project Registry
- Organization Registry
- Skill Registry
- Model/Provider Registry
- durable task state
- Event Bus
- shared mailbox
- trusted knowledge
- research evidence
- decisions and decision history
- model/skill performance
- current runtime state

### Context flow

```
START / TASK
    |
Context Gate
    |
Build context manifest
    |
Select authoritative records
    |
Attach provenance / freshness / scope
    |
Model execution
```

The system must expose **unknown** when authoritative information is absent. Generated model text is never automatically promoted to authoritative Aurora state.

## 6. Director bootstrap

Before a Director is considered READY for a new session:

1. Load Aurora identity and governance.
2. Load the complete Project Registry index.
3. Load Organization Registry and current agent topology.
4. Load current runtime/task state.
5. Read relevant shared-mailbox state.
6. Load active decisions and pending work.
7. Load relevant trusted knowledge and evidence.
8. Load model/provider availability and Director model policy.
9. Produce a context manifest with provenance and freshness.
10. Only then accept normal user conversation.

Deep project contents are retrieved on demand; the bootstrap must know the existence and canonical metadata of every project without injecting every byte into the prompt.

## 7. Model execution contract

No project model calls a provider directly.

```
Agent/Director
   -> Context Gate
   -> Model Router
   -> Provider Adapter
   -> Provider API / local runtime
   -> Execution telemetry
   -> Evaluation
   -> Learning / evidence stores
```

The same Context Gate rules apply whether the model is Gemini, Claude, NVIDIA or a local model.

## 8. Model/provider discovery

The registry is split into two concerns:

```
Provider Registry
   -> provider identity / adapter / endpoint / policy / credential reference

Model Registry
   -> model identity / provider / capabilities / context / specialties /
      health / availability / cost / latency / quality / observed performance
```

Provider discovery/synchronization may populate candidate models, but governance and validation must be required before a candidate becomes usable.

## 9. Shared mailbox and Event Bus

Do not create a second unrelated messaging system.

Extend the current durable Event Bus and MissionMemory into one logical communication/context plane.

Every event/message should preserve, where applicable:
- event/message id
- project id
- mission id
- task id
- agent id
- decision id
- skill id/version
- model id
- provider id
- timestamp
- correlation/causation identity
- source
- evidence/provenance reference
- status

The dashboard consumes events/read models; it does not become an alternative event journal.

## 10. Dashboard state model

The browser receives:
1. an authoritative snapshot;
2. then incremental events;
3. periodic reconciliation against authoritative state.

Displayed freshness must be visible. Event-driven delivery can use SSE or WebSockets later; transport is an implementation choice, not the architecture. The important contract is snapshot + ordered/recoverable updates + reconciliation.

## 11. Command safety

Dashboard commands must never:
- edit SQLite directly
- bypass Capability Broker
- bypass autonomy policy
- bypass Organization Registry
- bypass skill governance
- activate a model/provider without validation
- activate live execution without explicit authorization

All commands become normal Aurora actions with durable audit evidence.

## 12. Avatar

The central portrait is a presentation adapter for the Director.

```
User voice/text
   -> Director Conversation API
   -> Director context/bootstrap
   -> Director model
   -> response + speech/animation intent
   -> Avatar Adapter
   -> dashboard portrait
```

Replacing the avatar provider must not change Director memory, knowledge, projects, tasks or model routing.

## 13. Non-functional requirements

The dashboard must remain:
- truthful: authoritative state only
- inspectable: drill-down to evidence/events
- recoverable: reconnect and rebuild from snapshot
- provider agnostic
- project agnostic
- auditable
- permission aware
- resilient to stale/out-of-order events
- explicit about freshness and unknown state

Event-driven dashboards require deliberate correlation, delivery and recovery semantics; the UI should not be treated as the source of correctness. citeturn785641search2turn785641search3

## 14. Definition of done for the dashboard architecture

The architecture is considered fully implemented only when:

- every Aurora project is represented in Project Registry;
- a new Director can bootstrap canonical Aurora state before conversation;
- every project model execution passes the Context Gate;
- providers and models are represented in registries;
- model discovery can update candidates without bypassing governance;
- the shared mailbox has global/project/mission/agent views without duplicating the Event Bus;
- dashboard state can be reconstructed from authoritative snapshot + events;
- clicking an agent or project exposes the corresponding authoritative details;
- Director/model/provider changes remain independent of stored Aurora knowledge and experience;
- avatar replacement does not alter Aurora state;
- end-to-end tests prove restart, reconnect, provider swap and context integrity.

