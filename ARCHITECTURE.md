# Aurora Architecture v1

Aurora is an autonomous multi-agent engineering system. Core flow: Director -> Orchestration -> Agents -> Capability Broker -> Tool Resolver -> Acquisition -> Verification -> Runtime -> Audit/Reconciliation.

## Autonomy
Agents request named capabilities such as `python.package:pandas`, `tool:rg`, `browser:chromium`, or `repo:github`. The broker evaluates policy, resolves trusted sources, acquires dependencies, verifies them, and records provenance.

## Build vs runtime
The build environment bootstraps Aurora. The runtime is a separate trust domain with least privilege, sandboxing, workspace isolation, and policy-controlled networking.

## Required subsystems
Director, task engine, agents, memory, capability broker, tool registry, acquisition, verification, Internet gateway, sandbox, audit, bootstrap and recovery.

Autonomous does not mean unrestricted root or arbitrary shell execution.

## Adaptive mission loop

A completed task phase is not treated as sufficient evidence by default. The
DirectorRuntime builds review context from durable mission memory and task
results, asks the Director to choose `complete`, `continue`, or `blocked`,
and, when more work is required, persists a new numbered planning phase without
discarding earlier history. Review attempts are bounded and persisted as Event
Bus events.

Mission memory is a durable shared inbox/context layer backed by the same
SQLite trust boundary. Worker results are stored in bounded form so later
Director decisions can inspect actual outputs rather than only task status.
