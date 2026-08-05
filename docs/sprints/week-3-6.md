# Sprint Plan: Weeks 3–6 (Final)

## Decisions Locked

| Decision | Resolution |
|---|---|
| Policy re-synthesis frequency | Cache per endpoint; re-synthesize only when ERI changes (new telemetry arrives) |
| Strategy B logic | Adaptive backoff based on recent success rate. No state machine, no quarantine. Research baseline only — **not** the Tier 3 fallback |
| Strategy A | Static exponential backoff. Also serves as Tier 3 fallback when AEDE is unavailable |
| ExplainabilityRecord persistence | B owns (consistent with execution plan: "generation + persistence") |

---

## B (Sam) — Weeks 3–4: AEDE Core (COMPLETED ✅)

> [!NOTE]
> **Status**: 100% Complete — 48/48 unit & integration tests passing.
> **Associated Architecture Decision Records**:
> - [ADR 0011: Three-Tier Delivery Policy Fallback Hierarchy](file:///c:/Users/sammj/MajorProject/docs/adr/0011-three-tier-policy-fallback.md)
> - [ADR 0012: Endpoint State Machine Quarantine Terminality](file:///c:/Users/sammj/MajorProject/docs/adr/0012-quarantine-state-terminality.md)
> - [ADR 0013: Explainability Record Schema and Metric Preservation](file:///c:/Users/sammj/MajorProject/docs/adr/0013-explainability-record-schema.md)

### Task 1: `DeliveryPolicy` Entity
**File**: `relay/kernel/policies/delivery_policy.py`

The immutable contract between Control Plane and Data Plane. Pydantic model with sub-policies:
- `RetryPolicy` (initial_delay, max_delay, max_retries, backoff_strategy, deadline)
- `ConcurrencyPolicy` (max_parallel, queue_priority, throughput_limit)
- `IsolationPolicy` (continue | reduce_concurrency | pause | quarantine)
- `ReplayPolicy` (allowed, window, batch_size, speed)
- `NotificationPolicy` (notify_admin, alert_level, suppress)
- `IntegrityPolicy` (hmac_required, signature_ttl, replay_protection)

All frozen. All immutable.

---

### Task 2: `PolicyEvaluation` + `ExplainabilityRecord` Entities
**Files**:
- `relay/kernel/entities/policy_evaluation.py`
- `relay/control_plane/explainability/record.py`

`PolicyEvaluation`: one AEDE execution. Links the `ERIScore`, `EndpointState` (before/after), and the produced `DeliveryPolicy`.

`ExplainabilityRecord`: answers "why?" — which metrics triggered it, which thresholds crossed, what state transition occurred, what policy fields changed.

---

### Task 3: Policy Synthesizer
**File**: `relay/control_plane/aede/synthesizer.py`

Pure function. The core research contribution:
```python
def synthesize_policy(
    endpoint_state: EndpointState,
    eri: ERIScore,
    config: AdaptivePolicyConfig,
    endpoint_id: str,
    computed_at: datetime,
) -> tuple[DeliveryPolicy, ExplainabilityRecord]
```

Maps each state to a policy profile:
| State | Retry | Concurrency | Isolation |
|---|---|---|---|
| AVAILABLE | Immediate, minimal backoff | Max parallel | Continue |
| OBSERVED_DEGRADATION | Moderate backoff | Reduced | Reduce concurrency |
| UNSTABLE | Aggressive backoff | Single-stream | Pause new deliveries |
| QUARANTINED | No delivery | None | Quarantine |
| RECOVERING | Conservative intervals | Single-stream | Gradual restore |

---

### Task 4: Strategy A & B Definitions
**Files**: `relay/control_plane/aede/strategies/`

- **Strategy A** (`static.py`): Fixed exponential backoff. Always returns the same policy regardless of endpoint state. Also serves as Tier 3 fallback.
- **Strategy B** (`adaptive.py`): Scales retry interval inversely with recent success rate. No state machine awareness. `interval = base_delay / max(success_rate, 0.1)`, capped at `max_delay`.

Both produce `DeliveryPolicy` objects — same output contract as Strategy C (the full AEDE synthesizer).

---

### Task 5: `DeliveryStrategy` Interface
**File**: `relay/kernel/contracts/delivery_strategy.py`

Abstract contract that all three strategies implement. A's Retry Manager and Dispatcher consume this interface. Feature flags select which implementation is active.

---

### Task 6: Feature Flag System
**File**: `relay/infrastructure/feature_flags.py`

Reads from `config.yaml` or environment variables. Flags:
- `AEDE_ENABLED`, `ERI_ENABLED`, `STATE_MACHINE_ENABLED`
- `STATIC_RETRY_MODE`, `ADAPTIVE_RETRY_MODE`

---

### Task 7: Redis Policy Cache Interface
**File**: `relay/infrastructure/cache.py`

Defines `PolicyCache` protocol + Redis-backed implementation:
- `cache_policy(endpoint_id, policy)` — stores with 5-min TTL
- `get_cached_policy(endpoint_id)` → `DeliveryPolicy | None`
- `get_fallback_policy()` → static Strategy A defaults

A wires the three-tier fallback logic in the Dispatcher using this interface.

---

### Task 8: ExplainabilityRecord PostgreSQL Repository
**File**: `relay/infrastructure/persistence/explainability_repo.py`

Schema + Alembic migration for explainability records. `save()` and `get_by_policy_id()`.

---

### Task 9: Unit Tests for All Above
Cover:
- Policy Synthesizer produces correct policy per state
- Explainability records capture correct reason text and metric values
- Feature flag switching changes active strategy
- Strategy A/B produce valid `DeliveryPolicy` objects
- Redis cache TTL behavior (mock Redis)
- Three-tier fallback logic

---

### B Week 3–4 Estimated Timeline

| Day | Tasks |
|---|---|
| 1 | `DeliveryPolicy` entity + sub-policy models |
| 2 | `PolicyEvaluation` + `ExplainabilityRecord` entities |
| 3–4 | Policy Synthesizer (state → policy mapping + explainability generation) |
| 5 | Strategy A + Strategy B definitions |
| 6 | `DeliveryStrategy` interface + Feature Flag system |
| 7 | Redis policy cache interface + implementation |
| 8 | ExplainabilityRecord repository + Alembic migration |
| 9–10 | Full unit test suite for everything above |

---

## A — Weeks 3–4: Delivery Core

> [!IMPORTANT]
> A must pull `hotfix/rename-platform-to-relay` and rebase before starting. Root namespace is now `relay/`.

### Week 3 (Days 1–5)

- [ ] Pull hotfix, rebase feature branch onto `relay/` namespace
- [ ] Kafka consumer (Dispatcher): reads from `events.business`, creates `Delivery` + initial `DeliveryAttempt`
- [ ] `Delivery` and `DeliveryAttempt` PostgreSQL repositories (Alembic migration + `save()`/`get()`)
- [ ] Stateless webhook delivery worker: receives `DeliveryAttempt`, makes HTTP POST, collects telemetry
- [ ] Worker publishes `Telemetry` conforming to B's protocol (`relay/kernel/interfaces/telemetry_protocol.py`)

### Week 4 (Days 6–10)

- [ ] Strategy A retry execution in worker — read intervals from `DeliveryPolicy`, execute retries (implements `DeliveryStrategy` interface from B)
- [ ] Wire Simulator as delivery target (C's `POST /webhook`)
- [ ] End-to-end smoke test: SDK → Kafka → Dispatcher → Worker → Simulator → Telemetry stored
- [ ] Integration test: 5xx from Simulator triggers retry with correct backoff

**Deliverable**: events publish → Kafka → Worker → hits Simulator → retry on failure → telemetry stored in DB.

---

## A — Weeks 5–6: AEDE Integration

- [ ] Wire Dispatcher to request policy from AEDE before assigning work to workers
- [ ] Three-tier fallback wiring using B's `PolicyCache` interface (AEDE → Redis → static)
- [ ] Python SDK: `publish_event()`, `register_endpoint()` — thin REST wrapper
- [ ] Benchmark runner skeleton: automated (configure simulator → publish events → collect metrics → reset)
- [ ] Benchmark result storage to PostgreSQL

---

## C — Weeks 3–4: Simulator + Dashboard Foundation

### Week 3 (Days 1–5)

- [ ] Pull hotfix, rebase onto `relay/` namespace
- [ ] Complete Simulator with all four failure modes:
  - `http_error` → returns 500/502/503 (configurable)
  - `timeout` → holds connection until client times out
  - `slow` → responds 200 OK with configurable delay
  - `rate_limit` → returns 429
- [ ] `POST /simulator/config` accepts `{mode, rate}`, updates behavior
- [ ] Simulator logs each request with structured JSON

### Week 4 (Days 6–10)

- [ ] Dashboard backend: FastAPI WebSocket server pushing live updates
- [ ] Dashboard REST endpoints:
  - `GET /api/endpoints` — list endpoints with current state
  - `GET /api/endpoints/{id}/eri-history` — ERI scores over time
  - `GET /api/deliveries` — recent delivery attempts
  - `GET /api/explainability/{policy_id}` — explainability record
- [ ] React app scaffold (Vite) + WebSocket connection
- [ ] Fault injection controls: failure rate slider + error type dropdown → `POST /simulator/config`

---

## C — Weeks 5–6: Dashboard Panels

- [ ] Endpoint state panel (colored badge per state)
- [ ] ERI line chart (live, last N data points)
- [ ] Delivery attempt timeline (event ID, status, attempt count, timestamp)
- [ ] Policy decision panel (current policy fields + explainability text)
- [ ] Expandable explainability record viewer
- [ ] Benchmark chart placeholder (Strategy A vs B vs C)
- [ ] Dashboard UX pass: consistent layout, responsive

---

## Integration Seams

| Seam | Defined By | Consumed By | Status |
|---|---|---|---|
| `Telemetry` protocol | B ✅ | A (worker publishes) | **Done** |
| `DeliveryPolicy` entity | B (Week 3) | A (Dispatcher/Worker) | **Next** |
| `DeliveryStrategy` interface | B (Week 3) | A (Retry Manager) | **Next** |
| `PolicyCache` interface | B (Week 3) | A (three-tier fallback) | **Next** |
| Feature flag config | B (Week 3) | A (worker reads flags) | **Next** |
| `POST /simulator/config` | C (Week 3) | C dashboard + A smoke tests | C owns both |
| ExplainabilityRecord API | B (persistence) | C (dashboard displays) | Week 5–6 |
| Chart data API | B (Week 5–6) | C (benchmark charts) | Week 5–6 |

---

## Clash Verification ✅

| My Plan Item | Execution Plan Owner | Clash? |
|---|---|---|
| B defines Strategy A/B/C policies | B owns "Policy Synthesizer → immutable DeliveryPolicy" | ✅ No clash — A owns *execution* (Retry Manager), B owns *definition* (policy production) |
| B defines `PolicyCache` interface | A owns "Three-tier fallback wiring + Redis TTL" | ✅ No clash — B defines contract, A wires it into Dispatcher |
| B owns ExplainabilityRecord repo | A owns "PostgreSQL repositories" generally | ✅ No clash — B explicitly owns "Explainability records (generation + persistence)" |
| C builds dashboard REST endpoints | B owns "Chart data API" | ✅ No clash — C serves general dashboard data, B serves benchmark comparison charts specifically |
