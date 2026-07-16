# ADR 0003: Separate Control Plane from Data Plane

## Status

Accepted

## Context

The platform must make two fundamentally different kinds of decisions:

1. **What should happen** — Is this endpoint healthy? Should delivery be paused? Should retries be delayed? Should administrators be notified?
2. **What is happening** — Which event should be delivered? Which worker owns this partition? Has delivery succeeded?

These concerns can be handled in one of two ways:

**Option A — Unified execution**: Workers consult endpoint health, make retry decisions, and execute delivery in the same component. This is how most naive webhook implementations work — a worker checks some state, decides whether to retry, and sends the HTTP request.

**Option B — Separated control and data planes**: A dedicated decision-making component (Control Plane) determines policy. Separate execution components (Data Plane) carry out those policies without making decisions of their own.

The choice between these approaches has cascading consequences for scalability, testability, extensibility, and the research contribution.

For the research contribution specifically, the AEDE must be independently testable: given identical endpoint observations, it must always produce identical delivery policies. This property — determinism — is only guaranteed if policy computation is fully separated from execution.

If workers contain policy logic, then testing policy correctness requires testing through delivery infrastructure. If policy computation is isolated in AEDE, it can be unit tested in isolation with no network, database, or Kafka dependency.

## Decision

Adopt a strict **Control Plane / Data Plane separation**.

**Control Plane** (owns decisions):
- Adaptive Event Delivery Engine (AEDE)
- Endpoint Reliability Index computation
- Endpoint State Machine
- Policy Synthesizer
- Explainability Engine
- Endpoint Registry
- Subscription Service
- Administrative APIs and Dashboard

**Data Plane** (owns execution):
- Event Ingestion API
- Kafka publishing
- Dispatcher
- Worker Pool
- Retry Manager
- Replay Manager
- Delivery Adapters

**The contract between them is the Delivery Policy** — an immutable object produced by AEDE and consumed by the Data Plane. Workers never compute policies. The Control Plane never delivers events.

This is the same architectural pattern used by Kubernetes (API server + scheduler vs. kubelet + container runtime), service meshes (control plane vs. data plane proxies), and SDN networking.

## Consequences

**Positive:**
- **Independent scalability**: Workers scale horizontally without requiring additional policy computation capacity. Policy evaluation can be scaled independently of delivery throughput.
- **Testability**: AEDE is a pure function — given inputs (telemetry, endpoint state, configuration), it produces an output (Delivery Policy). This can be unit tested without any infrastructure dependency.
- **Extensibility**: New delivery adapters do not require changes to policy logic. New policies do not require changes to delivery adapters. Both evolve independently.
- **Explainability**: Every delivery action is traceable. Developers can inspect the Delivery Policy that caused any behavior before observing the executed action.
- **Replaceability**: Workers are stateless and replaceable. A crashed worker does not lose any decision state — that state lives in AEDE and PostgreSQL.
- **Research contribution isolation**: AEDE is a cleanly bounded component. Comparing delivery strategies (Strategy A vs B vs C) becomes a matter of changing the policy engine, not rewriting workers.

**Negative:**
- Increased architectural complexity. More components, more communication paths, more deployment units.
- Workers must fetch policies from AEDE before executing delivery, introducing a synchronous dependency in the delivery path.

**Mitigations — Three-Tier Policy Fallback:**

Workers follow a tiered fallback so AEDE is not a single point of failure for delivery:

```
Tier 1: AEDE live policy          — normal operation
         ↓ if AEDE unreachable
Tier 2: Redis-cached policy       — last known good policy per endpoint (configurable TTL)
         ↓ if cache cold or TTL expired
Tier 3: Static fallback policy    — safe hardcoded defaults (Strategy A equivalent)
```

The Tier 3 static fallback uses conservative, pre-configured defaults: exponential backoff starting at 5 seconds, capped at 300 seconds, single-stream delivery per endpoint, no quarantine decisions, and an administrative alert that AEDE is unavailable. This is functionally equivalent to the Strategy A research baseline — delivery continues with known, safe behavior. Adaptive intelligence is suspended, not the platform.

When AEDE recovers, it re-evaluates all active endpoints and resumes normal policy synthesis. Workers automatically promote back to Tier 1 on the next policy fetch.

Additional mitigations:
- AEDE is stateless and can be horizontally scaled behind a load balancer, reducing the probability of a full Tier 1 outage.
- The modular monolith deployment (Phase 1) runs both planes in the same process, making Tier 1 unavailability impossible during development.

The Control Plane / Data Plane separation is the architectural foundation of the entire research contribution. Without it, AEDE cannot be deterministically tested, independently evaluated, or cleanly compared against alternative strategies.
