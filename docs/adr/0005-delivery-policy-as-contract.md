# ADR 0005: Use Immutable Delivery Policies as the Contract Between Control Plane and Data Plane

## Status

Accepted

## Context

The Control Plane (AEDE) must communicate delivery intent to the Data Plane (Workers, Dispatcher). There are several approaches for this communication:

**Option A — Shared mutable state**: Workers read endpoint state and configuration directly from the database and compute their own behavior at runtime. Each worker queries: "What is the current ERI? What state is this endpoint in? How should I retry?"

**Option B — RPC/direct calls**: Workers call AEDE synchronously before each delivery to ask "what should I do?" AEDE responds with current instructions.

**Option C — Immutable policy objects**: AEDE synthesizes a complete, immutable Delivery Policy object. The Dispatcher fetches this policy once before creating a DeliveryAttempt. Workers execute the policy without any additional decisions.

The choice between these options determines whether workers can remain stateless, whether delivery behavior is auditable, and whether the Control Plane / Data Plane separation is real or nominal.

Option A violates the separation — workers that read endpoint state and compute behavior are effectively acting as decision makers. Every worker becomes a partial reimplementation of the policy engine. Policy changes require updating every worker.

Option B preserves separation but couples delivery execution to AEDE availability. If AEDE is slow or unavailable, every delivery stalls. It also provides no audit record of what instructions were given for a specific delivery attempt.

Option C cleanly separates concerns. AEDE synthesizes the policy once. The Dispatcher records which policy governed each DeliveryAttempt. Workers execute and never decide. The policy object becomes a permanent, queryable audit record.

## Decision

Use **immutable Delivery Policy objects** as the contract between the Control Plane and the Data Plane.

A Delivery Policy is an immutable object produced by AEDE before each DeliveryAttempt. It contains everything a Worker needs to execute delivery without making any runtime decisions:

```
DeliveryPolicy {
    policy_id             (globally unique)
    policy_version
    created_at
    endpoint_id
    endpoint_state        (at time of evaluation)
    eri_score             (at time of evaluation)
    retry_policy          (initial_delay, max_delay, max_retries, backoff_strategy, deadline)
    concurrency_policy    (max_parallel, queue_priority, throughput_limit)
    isolation_policy      (action: continue | reduce | pause | quarantine)
    replay_policy         (allowed, window, batch_size, speed)
    notification_policy   (notify_admin, alert_level, suppress)
    integrity_policy      (hmac_required, signature_ttl, replay_protection)
    explainability {
        reason
        metrics_snapshot
        thresholds_evaluated
        applied_policies
        eri_breakdown
    }
}
```

Every DeliveryAttempt references the exact policy that governed it. Historical behavior is permanently auditable. Workers are truly stateless — they receive a policy, execute it, and report telemetry.

Policies are versioned. Future AEDE versions introduce new fields. Workers ignore unknown fields (forward compatibility).

Workers cache the most recently received policy in Redis. If AEDE is briefly unavailable, workers continue executing the cached policy rather than halting.

## Consequences

**Positive:**
- Workers are completely stateless. They can crash, restart, and scale without losing any decision state — all decisions live in the policy object and AEDE.
- Every DeliveryAttempt is auditable: you can always retrieve the exact policy that produced it, regardless of how AEDE has evolved since.
- The policy object is a complete audit record. The Explainability Service can serve any historical policy decision without re-running AEDE.
- AEDE can be upgraded, replaced, or extended without touching workers. Workers only need to understand the Delivery Policy schema.
- Testing workers becomes trivial: inject a policy, assert behavior. No AEDE dependency needed in worker unit tests.

**Negative:**
- Policies may become stale between creation and execution. If endpoint state degrades sharply between policy evaluation and delivery attempt, the worker executes a policy that reflects a healthier state than current reality.
- The policy object schema must be versioned carefully to maintain backward compatibility.

**Accepted trade-offs:**
- Policy staleness is bounded. The Dispatcher always fetches the latest policy from AEDE before creating a new DeliveryAttempt. Staleness only occurs within the execution window of a single attempt, which is short (seconds). AEDE evaluates again after each attempt's telemetry is published.
- Schema versioning is a standard engineering discipline. The benefits of auditability, testability, and stateless workers far outweigh the maintenance overhead.

The Delivery Policy object is the most important architectural artifact in the platform. Every other design decision derives from it.
