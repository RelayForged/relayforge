# Sprint: Weeks 1–2

**Goal**: `POST /events` stores an event durably and publishes it to Kafka. Domain model in place well enough that Weeks 3–4 (Dispatcher, Worker, Simulator) can start immediately without rework.

---

## Pre-Week 1 — Kickoff (half day)

B (Sam) publishes the following before the session. A and C read, implement against them, do not modify without raising it with B first.

**Entity shapes — USE THESE** (B defines, A and C implement against):
- `Event`, `Endpoint`, `Subscription`, `Delivery`, `DeliveryAttempt` — Pydantic models, `frozen=True`
- `CorrelationID`, `CausationID` — value objects, attached to `Event` from day one
- Committed to `platform/kernel/entities/` and `platform/kernel/value_objects/` by B on Day 1

**ERI windowing — USE THIS**: attempt-count only, no time-based filter. See ADR 0009. Do not reintroduce a time-based window.

**Docker Compose — USE THIS**: Kafka in KRaft mode (no ZooKeeper), PostgreSQL, Redis.

**Kafka topic names — USE THESE** (only `events.business` is used this sprint, but all four are named now so nobody invents alternatives later):
- `events.business`
- `events.internal.delivery`
- `events.internal.policy`
- `events.internal.endpoint`

**Repo structure**: scaffolded per `CLAUDE.md` directory map, empty folders with `.gitkeep` committed. File placement is not up for discussion.

- [ ] B commits entity skeletons and value objects to shared branch
- [ ] A and C pull and confirm `docker compose up` shows all services healthy before splitting off

---

## Week 1

### Day 1 — All Three, Together

- [ ] Finalize and commit domain entities in `platform/kernel/entities/`: `Event`, `Endpoint`, `Subscription`, `Delivery`, `DeliveryAttempt` as Pydantic models — immutable (`model_config = ConfigDict(frozen=True)`)
- [ ] Finalize `platform/kernel/value_objects/`: `CorrelationID`, `CausationID` — attached to `Event` from day one
- [ ] Push to shared branch, everyone pulls before splitting off

### Day 2–5 — Split

#### Person A — Backend / Platform Core

- [ ] Docker Compose: Kafka, PostgreSQL, Redis with healthchecks — `docker compose up` reliably reports all green
- [ ] PostgreSQL schema migration for `Event` table (Alembic from day one — not raw SQL)
- [ ] PostgreSQL repository for `Event`: `save()`, `get_by_id()` — no other entities yet
- [ ] Kafka producer wrapper in `platform/infrastructure/messaging/` — thin abstraction so ingestion API never calls `kafka-python` directly

**Deliverable by end of Week 1**: a Python script that constructs an `Event`, saves it to Postgres, and publishes it to `events.business` — no API yet, just proving the pipe works end-to-end.

---

#### Person B — AEDE / Research Core (Sam)

- [ ] Write `config.yaml` with ERI weights and state machine thresholds exactly as specified in `docs/architecture.md`
- [ ] Build `platform/kernel/value_objects/ERIScore` — value object shape only, no computation logic yet
- [ ] Build `EndpointState` enum with all 5 states
- [ ] **Design the state transition table before writing any state machine code** — for every `(current_state, ERI_condition)` pair, what is the next state? Include all non-adjacent transitions explicitly:
  - `OBSERVED_DEGRADATION → QUARANTINED` (sudden collapse)
  - `OBSERVED_DEGRADATION → AVAILABLE` (brief blip, ERI recovers past recovery threshold)
  - `RECOVERING → QUARANTINED` (relapse)
  - `ANY → QUARANTINED` (manual admin override)
  - Document this as a table in `docs/adr/0008-endpoint-state-machine.md` or inline here before any code is written. This is the one piece of Week 1 most worth designing before coding.

**Deliverable by end of Week 1**: written, reviewed state transition table (not code) plus `ERIScore` and `EndpointState` skeletons committed to repo.

---

#### Person C — Dashboard + Simulator

- [ ] Scaffold Endpoint Simulator as its own FastAPI service — separate Docker Compose entry, its own directory (`simulator/`)
- [ ] `POST /simulator/config` accepting `{"failure_rate": float, "latency_ms": int, "error_type": string}` — `error_type` is an enum, not free text:
  - `http_5xx` → returns 500/502/503 (configurable)
  - `timeout` → holds connection until client times out
  - `slow` → responds 200 OK but with configured latency
  - `rate_limit` → returns 429
  - These must be distinct types now — they map to different ERI sub-metrics later
- [ ] `POST /webhook` (or configurable path) that actually behaves per current config — applies failure at the configured rate, succeeds otherwise

**Deliverable by end of Week 1**: Simulator runs standalone. `curl -X POST /simulator/config -d '{"failure_rate": 0.6, "error_type": "http_5xx"}'` then hitting the webhook endpoint repeatedly shows failures at roughly that rate.

---

### End of Week 1 — 30-Minute Sync

- Person A demos: `Event` → Postgres → Kafka script working
- Person B presents the state transition table. A and C read it and implement against it. This is the authoritative reference for the state machine — not a discussion, a handoff.
- Person C demos: Simulator responding correctly to config changes

---

## Week 2

#### Person A

- [ ] `Subscription` and `Endpoint` repositories (schema + Alembic migration + `save()`/`get()`)
- [ ] `POST /events` — takes payload, constructs `Event`, minimal validation (required fields only — full schema validation is deferred), saves to Postgres, publishes to Kafka, returns 202
- [ ] `POST /endpoints` — register an endpoint (URL + auth placeholder). No signing key logic yet — just enough to satisfy the FK that `Subscription` needs
- [ ] `POST /subscriptions` — minimal: `event_type` filter + `endpoint_id`, `ACTIVE` state only
- [ ] `GET /events/{id}` — prove the event is queryable after ingestion

**Deliverable by end of Week 2**: `curl -X POST /events` with a real payload → event persisted, appears on Kafka topic, queryable via `GET /events/{id}`.

---

#### Person B (Sam)

- [ ] Implement ERI computation function with configurable weights — feed it a fixed, hand-constructed list of fake `Telemetry` records (real pipeline data isn't available until Week 3–4). Hand-check 3–4 test cases against expected ERI scores before trusting the code.
- [ ] Implement state machine transition logic from the Week 1 table — pure function, no side effects:
  ```python
  def next_state(current: EndpointState, eri: ERIScore, consecutive_failures: int, ...) -> EndpointState
  ```
- [ ] Unit tests covering:
  - Hysteresis: feed oscillating ERI sequence (82, 78, 84, 79...) — confirm no state flapping
  - Every non-adjacent transition from the table
  - Determinism: same input sequence run twice produces identical output every time
  - Recovery conservatism: confirm recovery threshold is higher than degradation threshold

**Deliverable by end of Week 2**: ERI computation and state machine fully unit tested, demonstrably deterministic, running on fake data. Ready to wire to real telemetry in Week 3–4.

---

#### Person C

- [ ] Dashboard backend skeleton: FastAPI app + one WebSocket endpoint that pushes a test message on connect
- [ ] Dashboard frontend skeleton: React app connects to WebSocket, displays test message — proving the plumbing before building real panels
- [ ] Fault injection UI control (slider + dropdown) wired to call `POST /simulator/config` from Week 1 — full round trip: UI → Simulator config change → Simulator behavior changes

**Deliverable by end of Week 2**: open dashboard in a browser, drag the failure rate slider, Simulator's actual behavior changes (verify via curl or Simulator logs).

---

## End of Week 2 — Full Team Sync (checkpoint before Week 3–4)

Walk through the whole domain relationship chain with what actually exists in the repo:

- Person A shows: `POST /events` → Postgres → Kafka, plus endpoint and subscription registration
- Person B shows: ERI + state machine fully tested against fake data, non-adjacent transitions correct, deterministic
- Person C shows: Dashboard ↔ Simulator round trip working

**Before anyone leaves**: B publishes the ERI/state machine function interface to `platform/kernel/interfaces/` — what shape telemetry must be in when A's Worker hands it to B's ERI computation. This is the integration seam where Week 3–4 either goes smoothly or doesn't. B defines the Protocol; A implements against it. It goes in the repo before the meeting ends.
