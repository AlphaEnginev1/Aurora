# Aurora autonomous agent completion

This layer completes the five missing control surfaces without replacing the
existing Director, TaskEngine, Workbench, Router, GitHub, Research or Learning
subsystems.

## 1. Computer / OS agent

The existing `workbench:computer` path remains the authority. The local
workstation gateway now supports bounded GUI actions plus governed OS actions:
launch, argv-based shell execution, process listing/termination, download and
package installation. GUI and OS mutation remain explicit gateway policies.

## 2. Autonomous software engineering

`AutonomousSoftwareEngineer` provides the closed engineering lifecycle:
inspect, research, plan, edit, test, verify and publish, with bounded
iterations. Each phase is delegated to existing Aurora execution boundaries.

## 3. Autonomous mission manager

`AutonomousMissionManager` drives an existing `DirectorRuntime` mission to
a terminal result. It does not create another task engine or scheduler.

## 4. Discovery loop

`DiscoveryLoop` closes the hypothesis -> investigation -> falsification ->
novelty -> knowledge cycle. Research and learning implementations remain
injected existing Aurora services, so provenance and trust policy are not
bypassed.

## 5. Recursive self-improvement

`RecursiveSelfImprovement` provides the detect/diagnose/research/modify/test/
CI/learn control cycle. Code changes, tests, GitHub and learning remain the
existing project boundaries.

## Acceptance principle

The controllers are deliberately bounded and compositional. A production
runtime must inject the existing Workbench/Research/Learning/Director
implementations rather than bypassing them. No second Director, Router,
TaskEngine or Workbench is introduced.
