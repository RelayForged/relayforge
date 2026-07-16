# Relay — Execution Plan

3-person team. 3 months (~12 working weeks). AI-accelerated development.

---

## A. Scope Assessment

### What is achievable in 12 weeks (MVP)

| Component | Notes |
|---|---|
| Docker Compose environment | Kafka, PostgreSQL, Redis — local dev only |
| Core domain model | Event, Endpoint, Subscription, Delivery, DeliveryAttempt, DeliveryPolicy — Python dataclasses/Pydantic |
| FastAPI event ingestion API | `POST /events`, endpoint registration, subscription management |
| Python SDK | `publish_event()`, `register_endpoint()` — thin wrapper around the REST API |
| Kafka backbone | Producer (ingestion) + consumer (dispatcher) |
| Webhook delivery worker | Stateless. Executes policy. Collects telemetry. Makes no decisions. |
| ERI computation | Weighted composite score, configurable weights, sliding window |
| Endpoint State Machine | All 5 states, hysteresis, non-adjacent transitions |
| Policy Synthesizer | Produces immutable DeliveryPolicy objects from state + ERI |
| Explainability records | Stored to PostgreSQL, readable from dashboard |
| Retry Manager | Schedules retries. Intervals come from policy only. |
| PostgreSQL repositories | All primary entities persisted |
| Redis policy cache | Last-known-good policy per endpoint |
| Three-tier policy fallback | AEDE → Redis cache → static fallback (Strategy A defaults) |
| Endpoint Simulator | Controllable mock webhook receiver. Configurable failure rate, latency, error type via dashboard. |
| Live Dashboard | React frontend + FastAPI WebSocket backend. Endpoint state, ERI chart, delivery timeline, policy decisions, explainability panel, fault injection controls. |
| Benchmark framework | Strategy A vs B vs C. Run via dashboard or CLI. Produces comparison charts. |
| Feature flags | Switch delivery strategy without redeployment |

### What gets deferred (post-MVP)

These are not cut because they are unimportant. They are cut because the MVP is already ambitious and any one of these could consume weeks without advancing the research contribution.

| Deferred Item | Why |
|---|---|
| Kubernetes deployment | Docker Compose is sufficient for a demo environment |
| Terraform | No cloud infra to provision |
| CLI tool | Dashboard covers demo needs; SDK covers developer use |
| Multi-language SDKs (Java, Node, Go) | Python SDK demonstrates the concept |
| Notification Service (email/Slack) | Dashboard alerts replace this for MVP |
| Replay Manager | Stub with a basic version; full replay not needed for the demo |
| Full Subscription versioning | CREATED/ACTIVE/PAUSED is sufficient |
| Event Registry (schema validation) | Validates little in a demo context |
| Grafana/Prometheus | Dashboard has its own built-in charts |
| Multi-tenant / RBAC | Single-tenant demo is sufficient |
| OpenTelemetry tracing | Structured logs + dashboard observability is sufficient |

---

## B. 12-Week Timeline

### Week 1–2: Foundation

> Detailed day-by-day sprint plan: [`docs/sprints/week-1-2.md`](sprints/week-1-2.md)

Goal: a working skeleton with durable event publication.

- [ ] Docker Compose stack: Kafka, PostgreSQL, Redis — all healthy
- [ ] Domain entities in Python: Event, Endpoint, Subscription, Delivery, DeliveryAttempt (Pydantic models, no framework deps)
- [ ] FastAPI skeleton: `POST /events` endpoint, basic event ingestion logic
- [ ] Kafka producer wired to ingestion API
- [ ] PostgreSQL schema + repository for Event entity
- [ ] Smoke test: curl → `POST /events` → event stored in DB → message on Kafka topic

**Deliverable**: `POST /events` stores an event and publishes to Kafka.

---

### Week 3–4: Delivery Core

Goal: end-to-end delivery with a controllable simulator target.

- [ ] Kafka consumer (Dispatcher): reads events, creates Delivery + DeliveryAttempt
- [ ] Stateless webhook delivery worker: HTTP POST to endpoint URL, telemetry collection
- [ ] PostgreSQL repositories: Endpoint, Subscription, Delivery, DeliveryAttempt
- [ ] Basic telemetry stored after each attempt
- [ ] Strategy A retry — built flag-switchable from day one. Do not hardcode it. The delivery worker reads from a `DeliveryStrategy` interface; Strategy A is the first implementation. This shape must be compatible with B and C in Week 5–6 without refactoring.
- [ ] Endpoint Simulator: standalone FastAPI service, controllable via `POST /simulator/config`. Supports four distinct failure modes (see Simulator Semantics below).

**Deliverable**: events publish → Kafka → worker → hits simulator → retry on failure. All visible in DB.

#### Endpoint Simulator Failure Semantics

Specify this now — different failure types produce different ERI sub-metric values, so the simulator must support all of them to validate that ERI responds correctly to each fault signature.

| Mode | HTTP behaviour | ERI sub-metric affected |
|---|---|---|
| `http_error` | Returns 5xx (configurable: 500, 502, 503) | HTTP 5xx rate, success rate |
| `timeout` | Holds connection open until client times out | Timeout rate, average latency |
| `slow` | Responds 200 OK but with configurable delay (e.g. 3–8 seconds) | Average latency only — success rate unaffected |
| `rate_limit` | Returns 429 Too Many Requests | HTTP 5xx rate (treated as delivery failure for ERI), success rate |

Config API: `POST /simulator/config` with body `{"mode": "timeout", "rate": 0.6}` — applies `rate` fraction of requests to the given mode, the rest succeed normally. A `rate` of 1.0 means every request gets the failure mode.

This matters for the research: if the simulator only ever produces 5xx errors, you can only show ERI responding to one sub-metric. Supporting all four modes lets you demonstrate that timeout-heavy degradation looks different from error-heavy degradation in both the ERI and the explainability records.

---

### Week 5–6: AEDE Core

Goal: the research contribution is running and producing explainable decisions.

- [ ] ERI computation: configurable weights, **attempt-count-based window only** (last N attempts, no time-based filter — see ADR 0009). AEDE never calls `datetime.now()` internally.
- [ ] Endpoint State Machine: all 5 states, hysteresis (degradation threshold ≠ recovery threshold), non-adjacent transitions
- [ ] Policy Synthesizer: consumes state + ERI → produces immutable DeliveryPolicy
- [ ] Explainability records: every policy decision persisted to PostgreSQL with metrics, thresholds, and reason text
- [ ] Strategy B: adaptive backoff without state model (second research baseline) — wired into the same `DeliveryStrategy` interface as Strategy A
- [ ] Strategy C (AEDE): all three strategies switchable via feature flags with no worker changes
- [ ] Redis policy cache with 5-minute TTL; three-tier fallback wired and tested

**End of Week 5 checkpoint** — whole team reviews B's progress. Gate: ERI computation, state machine, and Policy Synthesizer must be functioning. If not, A and C immediately pair with B on explainability persistence and feature flag wiring (the most mechanical remaining tasks) so B can stay focused on any policy logic that needs fixing. This is an early warning trigger, not a failure condition.

**Deliverable (end of Week 6)**: AEDE running, policy decisions being made and persisted with full explainability. Feature flag switching works.

**If AEDE is not producing correct policies by end of Week 6** (schedule contingency): Weeks 7–8 dashboard work proceeds against Strategy A and B only. Strategy C integration becomes a Week 9–10 task alongside benchmark work. The dashboard does not depend on AEDE being correct — it depends on the policy API returning *something*, which Strategy A already provides. Do not delay dashboard development waiting for AEDE.

---

### Week 7–8: Dashboard + Live Demo Foundation

Goal: a browser-based demo that makes the research contribution visible in real time.

- [ ] Dashboard backend: FastAPI WebSocket server pushing live updates (state changes, ERI updates, delivery events)
- [ ] Dashboard frontend (React):
  - Endpoint state panel (current state, colored badge)
  - ERI line chart (live, last N minutes)
  - Delivery attempt list (event ID, status, attempt count, timestamp)
  - Policy decision panel (current policy fields + explainability text)
  - Fault injection controls: slider for simulator failure rate, latency, error type
- [ ] Fault injection API: dashboard calls `POST /simulator/config` on the Endpoint Simulator
- [ ] Explainability panel: expandable record per policy decision

**Deliverable**: open browser, set failure rate to 80%, watch ERI fall, watch state change to UNSTABLE, read the explainability record. No CLI required for demo.

---

### Week 9–10: Benchmark + Demo Polish

Goal: quantitative comparison that proves the research claim.

- [ ] Benchmark runner: automated experiment (configures simulator, runs events for N seconds, collects metrics, resets)
- [ ] Three scenarios automated:
  - Scenario 1: intermittent timeouts (random 30% failure, mixed with slow responses)
  - Scenario 3: burst failures (sudden 100% failure for 60 seconds, then recovery)
  - Scenario 10: slow degradation (success rate decreases linearly from 100% to 0% over 5 minutes — best shows AEDE advantage)
- [ ] Results stored to PostgreSQL
- [ ] Benchmark chart in dashboard: Strategy A vs B vs C on same axes (retry count, recovery time, delivery success rate)
- [ ] Redis three-tier fallback: demonstrable by stopping the AEDE process mid-run
- [ ] Dashboard UX pass: consistent layout, readable charts, clear state labels

**Deliverable**: benchmark run produces comparison charts. All three strategies visible on same graph. AEDE advantage is obvious on Scenario 10.

---

### Week 11–12: Integration, Report Writing, Demo Prep

Goal: stable system, complete written report, rehearsed demo.

**Week 11 — Report writing (all three contribute)**

The report writes largely from existing docs — do not start from scratch.

| Report chapter | Source |
|---|---|
| Introduction + Problem Statement | `docs/research.md` §Problem Statement |
| Related Work / Literature Gap | `docs/research.md` §Research Context + §Research Gap |
| System Design | `docs/architecture.md` (AEDE, ERI, State Machine, Event Lifecycle) |
| Research Hypothesis | `docs/research.md` §Research Hypothesis |
| Experimental Methodology | `docs/research.md` §Experimental Methodology |
| Evaluation Results | Benchmark charts from Week 9–10 runs + statistical summary |
| Discussion | Why AEDE outperforms A and B; weight framing (disclosed not optimized); threats to validity |
| Conclusion | Contributions summary; future work (time-based windows, ML extensions, Kubernetes) |

Each person drafts the chapters closest to their vertical (A: system design sections; B: AEDE + evaluation; C: dashboard + simulator description). All three review before submission.

- [ ] Introduction and Problem Statement drafted
- [ ] System Design chapter drafted (architecture diagrams from docs)
- [ ] Evaluation chapter drafted with actual benchmark results
- [ ] Discussion explicitly states: ERI weights are disclosed design parameters, not optimized — the contribution is the framework architecture, not the weights
- [ ] Threats to validity section included (see `docs/research.md`)

**Week 12 — Integration, demo rehearsal, buffer**

- [ ] End-to-end tests covering the full demo script path
- [ ] Demo script rehearsed by all three team members — minimum two full dry runs
- [ ] Bug fixes from rehearsal
- [ ] Report final review and submission
- [ ] Buffer for anything that slipped

**Deliverable**: demo script runs in under 10 minutes with no surprises. Report submitted.

---

## C. Team Split

Each person owns their vertical. All three own Docker Compose config, integration testing, and demo prep.

### Person A — Backend / Platform Core

- Domain model (Event, Endpoint, Subscription, Delivery, DeliveryAttempt)
- FastAPI ingestion API
- Kafka producer and Dispatcher
- Webhook delivery worker
- Retry Manager (all three strategies via `DeliveryStrategy` interface)
- PostgreSQL repositories
- Three-tier policy fallback wiring + Redis TTL
- Python SDK
- **Benchmark runner + scenario definitions** — scenarios are "drive controlled failure through the pipeline and record what happens," which sits squarely in A's existing context (Retry Manager, Dispatcher, Simulator interaction). A does not need to understand policy synthesis to implement this.
- Benchmark result storage (writes to PostgreSQL)

### Person B — AEDE / Research Core

- ERI computation + configurable weights (attempt-count window only)
- Endpoint State Machine (all 5 states, hysteresis, non-adjacent transitions)
- Policy Synthesizer → immutable DeliveryPolicy objects
- Explainability records (generation + persistence)
- Feature flag switching (Strategy A / B / C)
- Chart data API (reads benchmark results from PostgreSQL, formats for dashboard)
- Overall architecture decisions and cross-cutting concerns

> **Week 5 checkpoint (end of Week 5, not Week 6)**: All three team members review B's progress together. If ERI computation, the state machine, and the Policy Synthesizer are not functioning by this point, A or C pairs with B immediately on the two most mechanical remaining pieces — explainability record persistence and feature flag wiring — freeing B to focus on any policy logic that isn't yet correct. This checkpoint exists so a slip is caught with a full week of buffer remaining, not discovered at the Week 6 deadline.

> **Policy API contract**: B locks down the request/response shape between AEDE and the Dispatcher at the start of Week 5 — before the implementation is finished. A and C code against this contract independently. The contract is the boundary; the implementation can follow.

### Person C — Dashboard + Simulator

- Endpoint Simulator (FastAPI, all four failure modes: http_error, timeout, slow, rate_limit)
- Dashboard backend (FastAPI WebSocket)
- Dashboard frontend (React): all panels, charts, fault injection controls
- Benchmark visualization (comparison charts)
- Fault injection UI wiring

---

## D. Demo Script

The demo runs under 10 minutes live. All interactions happen in the browser (no terminal commands during the demo, except optionally showing the SDK script in a terminal).

**Setup (before demo starts)**: Docker Compose up, all services healthy, one registered endpoint pointing at the Endpoint Simulator, event publishing script ready but not running.

---

**Step 1 — Platform overview (1 min)**

Open the dashboard. Show:
- All services green (Kafka, PostgreSQL, Redis, AEDE, Simulator)
- The registered endpoint (URL pointing at Simulator, state: AVAILABLE, ERI: 100)
- No events yet

---

**Step 2 — Normal operation (1 min)**

Start the event publishing script (`python publish_events.py` — sends 1 event/second).

Show in dashboard:
- Events appearing in the delivery timeline
- ERI holding at ~100
- State: AVAILABLE
- Policy panel: "Immediate delivery, no backoff, max concurrency"
- Explainability record: "Success rate 100%, ERI 100 — AVAILABLE policy applied"

---

**Step 3 — Early degradation (1.5 min)**

On the dashboard, drag the failure rate slider to 60%.

Show in real time:
- Delivery attempts starting to fail (red entries in timeline)
- ERI beginning to drop (line chart falling)
- After enough failures, state transitions to OBSERVED_DEGRADATION
- Explainability record appears: "Success rate fell to ~40%, ERI = 72, transitioning to OBSERVED_DEGRADATION — monitoring frequency increased, parallel delivery reduced"
- Policy panel updates: reduced concurrency, moderate backoff

---

**Step 4 — Full instability (1.5 min)**

Increase failure rate to 85%.

Show in real time:
- ERI drops below unstable threshold
- State transitions to UNSTABLE
- Explainability record: "ERI = 45, consecutive failures threshold crossed — aggressive backoff applied, parallel delivery suspended, admin alert issued"
- Policy panel: long retry intervals, queue depth increasing, alert badge visible

---

**Step 5 — Quarantine (1 min)**

Increase failure rate to 100%.

Show:
- ERI collapses
- State transitions to QUARANTINED (non-adjacent transition from UNSTABLE bypassing intermediate states if applicable)
- Delivery halts — no new delivery attempts in timeline
- Policy: "Delivery suspended — all events preserved in queue"
- Events are still being published (SDK still running) — they accumulate, none are lost

---

**Step 6 — Recovery (1.5 min)**

Restore failure rate to 0% on the dashboard.

Show:
- State transitions to RECOVERING (first probe succeeds)
- Gradual traffic restoration — delivery attempts appear slowly
- ERI climbing back up
- Explainability record: "Consecutive successes threshold met — entering RECOVERING, gradual replay beginning"
- After sustained success: state returns to AVAILABLE
- Backlog of queued events begins delivering

---

**Step 7 — Benchmark comparison (1.5 min)**

Switch to the Benchmark tab.

Show a pre-run or live-run comparison of Strategy A vs B vs C on Scenario 10 (slow degradation):
- X-axis: time
- Y-axis: retry count and/or successful delivery rate
- Strategy A (static): keeps retrying at fixed intervals, burns retries during outage, slow recovery
- Strategy B (adaptive backoff): reduces retry frequency but no state awareness, no quarantine
- Strategy C (AEDE): detects degradation early, quarantines during outage, recovers efficiently — fewest wasted retries, fastest stable recovery

The chart makes the research claim visually obvious.

---

**Step 8 — Explainability panel (0.5 min)**

Click any policy decision entry in the delivery timeline.

Show the full explainability record:
- Metric values at decision time (success rate, ERI score, consecutive failures, latency)
- Which thresholds were crossed
- Which state transition occurred
- Which policy fields changed and why
- Timestamp and policy ID

"Every decision is fully auditable. No black box."

---

## E. MVP Definition (Explicit Stopping Point)

The project is "done enough" when all of the following are true:

- [ ] Events can be published via the Python SDK and reach Kafka
- [ ] Webhook delivery works with retry (at minimum Strategy A baseline)
- [ ] AEDE is computing ERI, running the state machine, and synthesizing policies
- [ ] Explainability records are stored and displayable in the dashboard
- [ ] Endpoint Simulator is controllable from the dashboard (failure rate, latency, error type)
- [ ] Dashboard shows live ERI chart, endpoint state, delivery timeline, policy decisions, and explainability panel
- [ ] Benchmark comparison (Strategy A vs B vs C) runs and produces charts
- [ ] Demo script runs end-to-end in under 10 minutes
- [ ] Three-tier policy fallback works (demoed by stopping the AEDE process — delivery continues via Redis cache, then static defaults)
- [ ] Report explicitly states ERI weights are disclosed design parameters, not optimized — the research contribution is the AEDE framework and its comparison against A/B baselines, not the weight values

Everything else is post-MVP. If all checklist items are met with time remaining, pick one deferred item. Otherwise: the project is done.

---

## F. What Not to Build

If an AI agent or team member proposes any of the following, it should be blocked until all MVP checklist items are complete. These are not missing features — they are explicitly deferred scope.

| Do Not Build | Alternative |
|---|---|
| Terraform or Kubernetes manifests | Docker Compose only |
| Grafana or Prometheus integration | Dashboard has built-in charts |
| Email or Slack notifications | Dashboard alert panel only |
| CLI tool | Python SDK + dashboard covers demo needs |
| Java, Node, or Go SDKs | Python SDK only |
| Multi-tenant support or organizational RBAC | Single-tenant, no auth beyond basic API keys |
| OpenTelemetry tracing | Structured JSON logs + dashboard observability |
| Subscription versioning beyond CREATED/ACTIVE/PAUSED | Not needed for demo |
| Full Replay Manager | Stub with basic version; full replay is post-MVP |
| Event Registry with formal schema validation | Type field on Event entity is sufficient |

If an agent starts generating Kubernetes YAML, Terraform HCL, Dockerfile.prod multi-stage builds for cloud deployment, or Grafana dashboard JSON — stop it. Those outputs are waste at this stage.
