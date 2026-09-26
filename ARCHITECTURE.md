# Aurora Architecture v1

Aurora is an autonomous multi-agent engineering system. Core flow: Director -> Orchestration -> Agents -> Capability Broker -> Tool Resolver -> Acquisition -> Verification -> Runtime -> Audit/Reconciliation.

## Autonomy
Agents request named capabilities such as `python.package:pandas`, `tool:rg`, `browser:chromium`, or `repo:github`. The broker evaluates policy, resolves trusted sources, acquires dependencies, verifies them, and records provenance.

## Build vs runtime
The build environment bootstraps Aurora. The runtime is a separate trust domain with least privilege, sandboxing, workspace isolation, and policy-controlled networking.

## Required subsystems
Director, task engine, agents, memory, capability broker, tool registry, acquisition, verification, Internet gateway, sandbox, audit, bootstrap and recovery.

Autonomous does not mean unrestricted root or arbitrary shell execution.