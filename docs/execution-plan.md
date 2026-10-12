# Relay — Execution Plan

3-person team. 3 months (~12 working weeks). AI-accelerated development.

---

## A. Scope Assessment

### What is achievable in 12 weeks (Target Scope)

| Component | Notes |
|---|---|
| Docker Compose environment | Kafka, PostgreSQL, Redis, Prometheus, Grafana |
| Core domain model | Event, Endpoint, Subscription, Delivery, DeliveryAttempt, DeliveryPolicy — Python dataclasses/Pydantic |
| FastAPI event ingestion API | `POST /events`, endpoint registration, subscription management |
| Python SDK | `publish_event()`, `register_endpoint()`, `replay_dead_letters()` — wrapper around the REST API |
| Kafka backbone | Producer (ingestion) + consumer (dispatcher) + DLQ topic (`events.deadletter`) |
| Webhook delivery worker | Stateless. Executes policy. Collects telemetry. Makes no decisions. |
| ERI computation & Weight Optimization | Weighted composite score; **weights optimized by trial and error** across 10 failure scenarios |
| Endpoint State Machine | All 5 states, hysteresis, non-adjacent transitions |
| Policy Synthesizer | Produces immutable DeliveryPolicy objects from state + ERI |
| Explainability records | Stored to PostgreSQL, readable from dashboard |
| Retry Manager & DLQ Router | Schedules retries. Routes exhausted deliveries to Dead Letter Queue (DLQ). |
| Replay Manager | Replays dead-lettered events creating fresh DeliveryAttempts; never mutates history |
| PostgreSQL repositories | All primary entities persisted (including Explainability and DLQ) |
| Redis policy cache | Last-known-good policy per endpoint with TTL |
| Three-tier policy fallback | AEDE → Redis cache → static fallback (Strategy A defaults) |
| Endpoint Simulator | Controllable mock webhook receiver. Configurable failure rate, latency, error type. |
| Live Dashboard & Visualizations | React frontend + FastAPI WebSocket backend. Dynamic ERI bands, delivery timeline, policy decisions, explainability radar, DLQ replay controls. |
| Benchmark framework | Strategy A vs B vs C. Automated runner across 10 failure scenarios with comparison charts. |
| Prometheus & Grafana | `/metrics` instrumentation, Prometheus scraper, provisioned Grafana dashboards. |
| Kubernetes Manifests | Production K8s manifests (`deploy/k8s/`), HPA scaling on Kafka lag, multi-stage Dockerfiles. |
| Feature flags | Switch delivery strategy without redeployment |

### What gets deferred (post-MVP)

The following remain deferred to focus on core platform delivery and research validation:

| Deferred Item | Why |
|---|---|
| Terraform | Kubernetes manifests and Docker Compose cover deployment needs |
| CLI tool | Dashboard + Python SDK cover demo and developer needs |
| Multi-language SDKs (Java, Node, Go) | Python SDK demonstrates the concept |
| External Notification Service (email/Slack) | Dashboard alert panel and Prometheus alertmanager cover MVP |
| Full Subscription versioning | CREATED/ACTIVE/PAUSED is sufficient |
| Event Registry (schema validation) | Validates little in a demo context |
| Multi-tenant / RBAC | Single-tenant demo is sufficient |
| OpenTelemetry distributed tracing | Structured JSON logs + Prometheus metrics + Dashboard observability is sufficient |

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
- [ ] Endpoint Simulator: standalone FastAPI service, controllable via `POST /simulator/config`. Supports six distinct failure modes (see Simulator Semantics below).

**Deliverable**: events publish → Kafka → worker → hits simulator → retry on failure. All visible in DB.

#### Endpoint Simulator Failure Semantics

Specify this now — different failure types produce different ERI sub-metric values, so the simulator must support all of them to validate that ERI responds correctly to each fault signature.

| Mode | HTTP behaviour | ERI sub-metric affected |
|---|---|---|
| `http_error` | Returns 5xx (configurable: 500, 502, 503) | HTTP 5xx rate, success rate |
| `timeout` | Holds connection open until client times out | Timeout rate, average latency |
| `slow` | Responds 200 OK but with configurable delay (e.g. 3–8 seconds) | Average latency only — success rate unaffected |
| `rate_limit` | Returns 429 Too Many Requests | HTTP 5xx rate (treated as delivery failure for ERI), success rate |
| `connection_reset` | Closes TCP connection before responding | Timeout rate, consecutive failures, success rate |
| `auth_failure` | Returns 401 or 403 (configurable) | Success rate, HTTP error rate |

Config API: `POST /simulator/config` with body `{"mode": "timeout", "rate": 0.6}` — applies `rate` fraction of requests to the given mode, the rest succeed normally. A `rate` of 1.0 means every request gets the failure mode.

This matters for the research: if the simulator only ever produces 5xx errors, you can only show ERI responding to one sub-metric. Supporting all six modes lets you demonstrate that timeout-heavy degradation looks different from error-heavy degradation in both the ERI and the explainability records, and covers all 10 benchmark failure scenarios.

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
- [ ] All 10 failure scenarios automated (each is a time-sequenced simulator configuration profile):
  - Scenario 1: intermittent timeouts (random 30% failure, mixed with slow responses)
  - Scenario 2: high latency (gradually increasing response delay)
  - Scenario 3: burst failures (sudden 100% failure for 60 seconds, then recovery)
  - Scenario 4: endpoint outage (extended unavailability)
  - Scenario 5: partial recovery (alternating success and failure)
  - Scenario 6: credential failure (HTTP 401/403 responses)
  - Scenario 7: connection failure (connection reset / TLS error)
  - Scenario 8: rate limiting (HTTP 429 responses)
  - Scenario 9: network partition (endpoint unreachable, connection timeout)
  - Scenario 10: slow degradation (success rate decreases linearly from 100% to 0% over 5 minutes — best shows AEDE advantage)
- [ ] **Two-Stage AEDE Weight Optimization Methodology**:
  - **Stage 1 (Rapid In-Process Synthetic Calibration)**:
    - *Why synthetic data works & is scientifically necessary*: Guarantees strictly identical failure/latency sequences across Strategy A, B, and C (eliminating live network jitter confounders), enables safe fault injection (100% 500 bursts, TCP resets, partitions), and evaluates dozens of candidate weight configurations in seconds in-process.
    - Sweeps the 7 sub-metric weights ($W_s, W_l, W_f, W_t, W_{5xx}, W_q, W_r$) across Scenarios 1–10 to eliminate configurations that cause state flapping or delayed quarantine, converging on the optimal parameter set.
  - **Stage 2 (Live End-to-End Real Pipeline Validation)**:
    - Deploys the tuned weights to the live Docker Compose environment.
    - Streams real HTTP events via the Python SDK through FastAPI → Kafka → Dispatcher → Webhook Worker (`httpx` making real socket calls) → Endpoint Simulator.
    - Verifies that real network socket timeouts, real HTTP status codes, and measured round-trip latencies trigger identical, stable adaptation on real live sockets.
- [ ] Results stored to PostgreSQL
- [ ] Benchmark chart in dashboard: Strategy A vs B vs C on same axes (retry count, recovery time, delivery success rate)
- [ ] Redis three-tier fallback: demonstrable by stopping the AEDE process mid-run
- [ ] Dashboard UX pass: consistent layout, readable charts, clear state labels

**Deliverable**: benchmark run produces comparison charts. All three strategies visible on same graph. AEDE advantage is obvious on Scenario 10. Tuned weights validated on both synthetic scenarios and live HTTP pipeline.

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
| Discussion | Why AEDE outperforms A and B; empirical trial-and-error optimization of ERI weights across failure scenarios; threats to validity |
| Conclusion | Contributions summary; future work (time-based windows, ML extensions, Kubernetes) |

Each person drafts the chapters closest to their vertical (A: system design sections; B: AEDE + simulator + evaluation; C: dashboard frontend description). All three review before submission.

- [ ] Introduction and Problem Statement drafted
- [ ] System Design chapter drafted (architecture diagrams from docs)
- [ ] Evaluation chapter drafted with actual benchmark results
- [ ] Discussion explicitly details the empirical trial-and-error optimization methodology of ERI weights across the 10 failure scenarios and why the architectural framework supports deterministic tuning
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

### Person A — Backend / Platform Core & Infrastructure

- Domain model (Event, Endpoint, Subscription, Delivery, DeliveryAttempt)
- FastAPI ingestion API (`POST /events`, `/endpoints`, `/subscriptions`)
- Kafka producer and Dispatcher
- Webhook delivery worker
- Retry Manager (executing retries derived from `DeliveryPolicy`)
- **Dead Letter Queue (DLQ)**:
  - Kafka DLQ topic (`events.deadletter`)
  - Automatic dead-letter routing upon retry exhaustion or terminal non-retryable errors
  - Persistence of `DEAD_LETTER` delivery state in PostgreSQL
- **Replay Manager**: Data plane consumer that executes replays from DLQ without mutating event history
- **Kubernetes Production Infrastructure**:
  - Production multi-stage Dockerfiles (`deploy/docker/`)
  - Complete Kubernetes manifests (`deploy/k8s/`): Deployments, StatefulSets, Services, ConfigMaps, Secrets, Ingress
  - Horizontal Pod Autoscaler (HPA) scaling based on CPU and Kafka consumer lag
- **Prometheus Metrics Registration**:
  - Expose `/metrics` on Ingestion API and Workers (throughput, latency histograms, error counts)
- PostgreSQL repositories for all entities (Events, Deliveries, Attempts, Telemetry, DLQ)
- Three-tier policy fallback data-plane wiring + Redis TTL

### Person B — AEDE / Research Core (Sam)

- ERI computation + sliding attempt-count window
- **Endpoint Simulator (Backend & Fault Injection Engine)**:
  - Standalone FastAPI mock webhook receiver (`relay/tests/test_simulator.py` / standalone service)
  - Supports all 6 distinct failure modes: `http_error` (500/502/503), `timeout`, `slow` (delay), `rate_limit` (429), `connection_reset`, `auth_failure` (401/403)
  - Configurable failure rate and delay profiles via `POST /simulator/config`
- **Empirical Optimization of AEDE Weights (Trial and Error)**:
  - **AEDE weights must be optimized by trial and error** across the 10 failure scenarios using a two-stage hybrid methodology:
    - **Stage 1 (Rapid In-Process Synthetic Calibration)**:
      - Uses deterministic synthetic telemetry generation across Scenarios 1–10 to evaluate dozens of weight candidate combinations in seconds.
      - *Why synthetic data works & is scientifically required*: Guarantees strictly identical failure sequences across Strategy A, B, and C (eliminating live network jitter confounders), enables safe execution of severe faults (100% 500 bursts, TCP resets, 4.5s partitions), and rapidly filters out weight combinations causing state flapping or delayed quarantine.
      - Iteratively tunes the 7 sub-metric weights ($W_{success}, W_{latency}, W_{fail}, W_{timeout}, W_{5xx}, W_{queue}, W_{recovery}$) to achieve optimal degradation detection and hysteresis stability.
    - **Stage 2 (Live End-to-End Real Pipeline Validation)**:
      - Deploys the calibrated weights to the live Docker Compose environment.
      - Streams real HTTP events via the Python SDK through FastAPI → Kafka → Dispatcher → Webhook Worker (`httpx` making real TCP/HTTP requests) → Endpoint Simulator.
      - Verifies that real measured network latencies and actual HTTP response codes produce the identical adaptive transitions observed in Stage 1.
- **Simulation & Adaptive Validation Loop**:
  - **Simulate a webhook registration** pointing to the Endpoint Simulator (via Python SDK or REST API).
  - **Simulate failure** using the Endpoint Simulator (timeouts, 5xx bursts, connection drops, slow responses).
  - **Show that the system adapts**:
    - Real-time ERI drop
    - State transitions: `AVAILABLE` → `OBSERVED_DEGRADATION` → `UNSTABLE` → `QUARANTINED`
    - Dynamic policy adjustment: concurrency throttling, exponential backoff with jitter, delivery suspension during quarantine
    - Smooth recovery probing during `RECOVERING` with hysteresis protection
- Dispatcher ↔ AEDE Three-Tier Policy Fallback integration (`PolicyFallbackManager`: AEDE → Redis Cache → Static Strategy A)
- Endpoint State Machine (all 5 states, hysteresis, non-adjacent transitions)
- Policy Synthesizer → immutable `DeliveryPolicy` objects
- Replay Policy synthesis (AEDE determines replay rate limits, batch sizes, and isolation)
- Explainability records (generation + PostgreSQL persistence)
- Feature flag switching (Strategy A / B / C)
- Chart Data API (reads benchmark and ERI results, formats for dashboard)
- Python SDK (`RelayClient`, `publish_event()`, `register_endpoint()`, `replay_dead_letters()`)
- Benchmark runner + 10 failure scenarios
- **Grafana Research Dashboards**:
  - Provisioned dashboards comparing Strategy A vs B vs C performance, retry amplification factor, and network/compute waste ratio

### Person C — Dashboard Frontend & Visualizations (Suhan)

- **React Dashboard Frontend (Strictly Frontend Work)**:
  - Complete UI component hierarchy, layout, and modern CSS styling (`relay/dashboard/frontend/`)
  - Real-time UI state management and WebSocket client connection to backend stream
- **Advanced Dashboard Visualizations**:
  - **Dynamic ERI Trajectory Chart**: Real-time ERI curve overlaid on color-coded background bands corresponding to the 5 states (`AVAILABLE`, `OBSERVED_DEGRADATION`, `UNSTABLE`, `QUARANTINED`, `RECOVERING`) and the hysteresis recovery line.
  - **Benchmark Comparative Visualizer**: Multi-series charts comparing Strategy A vs B vs C across all 10 failure scenarios (retry count, recovery latency, delivery success rate).
  - **Explainability Radar / Sub-metric Breakdown**: Visual component breaking down the 7 sub-metric scores and weight contributions explaining why decisions occurred.
  - **DLQ & Replay Dashboard Panel**: Interactive table of dead-lettered events with one-click replay trigger.
- **Fault Injection UI Controls**:
  - Sliders for failure rate, latency inputs, and error mode dropdowns wired to call Person B's Simulator Config API (`POST /simulator/config`)
- **Grafana Integration**:
  - Embedding / linking Grafana dashboards within the dashboard interface for operators

---

## D. Demo Script

The demo runs under 10 minutes live. All interactions happen in the browser (no terminal commands during the demo, except optionally showing the SDK script in a terminal).

**Setup (before demo starts)**: Docker Compose up (Kafka, PostgreSQL, Redis, Prometheus, Grafana, AEDE, Simulator), all services healthy, event publishing script ready.

---

**Step 1 — Platform overview & Webhook Simulation (1 min)**

Open the dashboard. Show:
- All services green (Kafka, PostgreSQL, Redis, Prometheus, Grafana, AEDE, Simulator)
- **Simulate Webhook Registration**: Register a target webhook endpoint pointing at the Endpoint Simulator (`http://simulator:8080/webhook`) via the UI or SDK script.
- Endpoint initializes in `AVAILABLE` state with `ERI = 100`.

---

**Step 2 — Normal operation (1 min)**

Start event publishing via Python SDK (`client.publish_event(...)` — sends 1 event/second).

Show in dashboard:
- Events appearing in the delivery timeline
- ERI holding at ~100
- State: `AVAILABLE`
- Policy panel: "Immediate delivery, no backoff, max concurrency"
- Explainability record: "Success rate 100%, ERI 100 — AVAILABLE policy applied"

---

**Step 3 — Simulate Failure: Early degradation & Adaptation (1.5 min)**

On the dashboard, configure the Simulator to inject failure: drag failure rate slider to 60% with mixed timeouts.

**Show that the system adapts in real time**:
- Delivery attempts starting to fail (red entries in timeline)
- ERI beginning to drop on the dynamic line chart
- As failure count mounts, state transitions to `OBSERVED_DEGRADATION`
- Explainability record appears: "Success rate fell to ~40%, ERI = 72, transitioning to OBSERVED_DEGRADATION — monitoring frequency increased, parallel delivery reduced"
- Policy panel updates: reduced concurrency, moderate backoff with jitter

---

**Step 4 — Full instability (1.5 min)**

Increase Simulator failure rate to 85% with 500/503 errors.

Show system adaptation:
- ERI drops below unstable threshold
- State transitions to `UNSTABLE`
- Explainability record: "ERI = 45, consecutive failures threshold crossed — aggressive backoff applied, parallel delivery suspended, admin alert issued"
- Policy panel: long retry intervals, queue depth increasing, alert badge visible

---

**Step 5 — Quarantine & Dead-Letter Queue (DLQ) Routing (1.5 min)**

Increase failure rate to 100%.

Show:
- ERI collapses (< 30)
- State transitions to `QUARANTINED`
- Delivery halts — no new HTTP delivery attempts hit the external target
- Events are still published via the SDK and accumulate durably in Kafka without loss
- Exhausted retry attempts transition to `DEAD_LETTER` and route to the DLQ topic (`events.deadletter`)
- Dead-lettered events appear in the dashboard's DLQ panel

---

**Step 6 — Recovery & Replay from DLQ (1.5 min)**

Restore Simulator failure rate to 0% on the dashboard.

Show:
- State transitions to `RECOVERING` as probe delivery succeeds
- Gradual traffic restoration — delivery attempts appear slowly under single-stream concurrency
- ERI climbs back up above recovery threshold (90) due to hysteresis
- State returns to `AVAILABLE`
- **Replay from DLQ**: Trigger replay of dead-lettered events via the dashboard DLQ panel (or SDK `client.replay_dead_letters()`)
- Replay creates fresh `DeliveryAttempt` records, delivering the queued events safely

---

**Step 7 — Prometheus & Grafana Live Metrics (1 min)**

Switch to Grafana:
- Show real-time platform metrics: ERI gauge, state transition distribution, delivery latency p95/p99, throughput, and DLQ arrival rate.

---

**Step 8 — Benchmark comparison & Optimized Weights (1 min)**

Switch to the Benchmark tab.

Show comparison of Strategy A vs B vs C on Scenario 10 (slow degradation) using the **empirically optimized AEDE weights**:
- Strategy A (static): keeps retrying at fixed intervals, burns retries during outage, slow recovery
- Strategy B (adaptive backoff): reduces retry frequency but lacks state awareness and quarantine
- Strategy C (AEDE with optimized weights): detects degradation early, quarantines during outage, recovers efficiently — fewest wasted retries, fastest stable recovery

"Every decision is fully auditable. No black box."

---

## E. MVP Definition (Explicit Stopping Point)

The project is "done enough" when all of the following are true:

- [ ] Events can be published via the Python SDK and reach Kafka
- [ ] Webhook delivery works with retry (at minimum Strategy A baseline)
- [ ] AEDE is computing ERI, running the state machine, and synthesizing policies
- [ ] **AEDE weights are empirically optimized by trial and error** across the 10 failure scenarios to prove sensitive detection and hysteresis stability
- [ ] Webhook registration and failure simulation demonstrate clear, automated system adaptation
- [ ] Dead Letter Queue (DLQ) captures exhausted deliveries, and Replay from DLQ works without mutating history
- [ ] Prometheus metrics are exposed on `/metrics` and provisioned in Grafana dashboards
- [ ] Kubernetes production deployment manifests are complete and validated
- [ ] Explainability records are stored and displayable in the dashboard
- [ ] Endpoint Simulator is controllable from the dashboard (failure rate, latency, error type)
- [ ] Dashboard shows live ERI chart with threshold bands, endpoint state, delivery timeline, policy decisions, explainability panel, and DLQ controls
- [ ] Benchmark comparison (Strategy A vs B vs C) runs and produces charts
- [ ] Demo script runs end-to-end in under 10 minutes
- [ ] Three-tier policy fallback works (demoed by stopping the AEDE process — delivery continues via Redis cache, then static defaults)

---

## F. What Not to Build

If an AI agent or team member proposes any of the following, it should be blocked until all target checklist items are complete:

| Do Not Build | Alternative |
|---|---|
| Terraform HCL provisioning | Kubernetes manifests and Docker Compose cover deployment needs |
| CLI tool | Python SDK + Dashboard covers developer and demo needs |
| Java, Node, or Go SDKs | Python SDK only |
| External email/Slack notification infrastructure | Dashboard alerts + Prometheus Alertmanager cover operational alerting |
| Multi-tenant support or organizational RBAC | Single-tenant demo environment is sufficient |
| OpenTelemetry distributed tracing | Structured JSON logs + Prometheus metrics + Dashboard observability is sufficient |
| Event Registry with formal schema validation | Type field on Event entity is sufficient |

