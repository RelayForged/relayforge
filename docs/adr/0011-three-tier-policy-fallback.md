# ADR 0011: Three-Tier Delivery Policy Fallback Hierarchy

## Status

Accepted

## Context

In the Relay Event Delivery Platform, the Control Plane (AEDE) continuously computes Endpoint Reliability Index (ERI) scores, manages endpoint state transitions, and synthesizes immutable `DeliveryPolicy` objects. The Data Plane (Dispatcher & Workers) consumes these policies to execute outbound deliveries.

According to the Core Architectural Law:
> **The Control Plane decides. The Data Plane executes.**

However, in production environments, Control Plane processes or background services (such as AEDE or PostgreSQL storage) may experience temporary network partitions, process crashes, or restarts. 

If workers were dependent on synchronous live calls to AEDE for every delivery attempt, an outage in the Control Plane would immediately cause a total platform delivery halt. Conversely, if workers were to fall back to computing delivery backoffs or intervals internally, the Core Architectural Law would be violated.

Therefore, a robust fallback strategy is required that keeps the Data Plane executing safely without introducing runtime policy decision logic into workers.

## Decision

We adopt a **Three-Tier Policy Fallback Hierarchy** managed by `PolicyFallbackManager` in the infrastructure layer (`relay/infrastructure/cache.py`).

```
Tier 1: AEDE Live Policy Synthesizer (Normal Operation)
          │ (If AEDE process/service unreachable)
          ▼
Tier 2: Redis-Cached Policy (Last Known Policy per Endpoint, 300s TTL)
          │ (If cache miss or TTL expired)
          ▼
Tier 3: Static Fallback Policy (Safe Hardcoded Strategy A Defaults)
```

### Hierarchy Breakdown

1. **Tier 1 — AEDE Live Policy**:
   - Primary operational mode.
   - Dispatcher requests live policy evaluation from AEDE upon receiving telemetry updates or dispatch events.
   - Policy is immediately written to Redis cache (`PolicyCache.set_policy()`) with a strict TTL of **300 seconds (5 minutes)**.

2. **Tier 2 — Redis-Cached Policy**:
   - Secondary operational mode invoked when AEDE live synthesis fails or times out.
   - Retrieves the last known synthesized policy for the targeted endpoint from Redis.
   - **TTL Enforcement**: Policies in cache strictly expire after 300 seconds (`POLICY_CACHE_TTL_SECONDS`). This prevents stale "AVAILABLE - deliver immediately" policies from persisting indefinitely if an endpoint degrades during an AEDE outage.

3. **Tier 3 — Static Fallback Policy**:
   - Tertiary operational mode invoked when Redis cache is empty or key has expired.
   - Serves safe, static default parameters (functionally equivalent to Strategy A baseline):
     - `retry_policy`: `initial_delay_seconds=5.0`, `backoff_multiplier=2.0`, `max_delay_seconds=300.0`, `max_retries=10`
     - `concurrency_policy`: `max_parallel=1` (conservative single-stream delivery)
     - `isolation_policy`: `action=CONTINUE` (never quarantine without AEDE confirmation)
     - `notification_policy`: `notify_admin=True` (alerts operators that Control Plane policy fallback was engaged)

## Consequences

- **High Availability**: Control Plane restarts or network blips degrade delivery intelligence gracefully without causing delivery outages or dropped events.
- **Strict Boundary Preservation**: Workers never compute retry intervals or backoff multipliers — they simply request a policy from `PolicyFallbackManager`, maintaining Control/Data plane separation.
- **Safety**: Tier 3 static fallback uses single-stream delivery (`max_parallel=1`) and raises admin alerts without making destructive state changes (e.g. automatically quarantining an endpoint without AEDE validation).
- **Stale State Protection**: 5-minute TTL guarantees that outdated policies are evicted if AEDE remains offline for an extended duration.
