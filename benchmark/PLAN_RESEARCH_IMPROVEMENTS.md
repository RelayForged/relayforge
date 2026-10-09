# Strengthen the AEDE research contribution — full improvement plan (v2)

## Context

The research-integrity remediation (`benchmark/PLAN_RESEARCH_INTEGRITY.md`) fixed weight-selection
leakage and report/code drift. This plan adds further improvements identified in a follow-up audit.

**Revision note (v2):** an earlier draft of this plan scoped two items conservatively — reporting the
genetic optimizer's weights alongside the hand-picked ones without adopting them, and only fixing the
demo script's honesty rather than wiring it to real infrastructure. The user explicitly rejected both:
the GA-discovered weights should actually become the production weights if they pass the same
acceptance bar, and the demo should actually work end-to-end against real infrastructure, not just be
honest about not doing so. Both are now full scope items below (§3 and §6), grounded in the real
codebase (not hand-waved) — see the investigation notes in each section for exact call sites.

This plan is written for a different agent to execute — self-contained, no need for this
conversation's history.

## Priority order

1. Expand the held-out evaluation set.
2. Free / near-zero-cost documentation fixes.
3. Adopt genetic-optimizer-discovered weights as production, if they clear the acceptance bar.
4. Leave-one-out calibration robustness check.
5. Ablation study, oracle baseline, in-process harness-vs-real-service validation.
6. Wire the live dashboard to real delivery via Kafka, then fix the demo to use it for real.

---

## 1. Expand the held-out evaluation set

**Why:** the held-out set is currently only 5 scenarios (`{3,4,5,9,10}`). It also doesn't exercise the
`RECOVERING → QUARANTINED` re-quarantine transition (defined in `architecture.md` and
`relay/control_plane/state_machine/transitions.py`, never triggered by any of the current 11
scenarios), or clustered/non-i.i.d. failure patterns (flagged as an External Validity threat in
`docs/research.md` but never tested).

**Add three new scenarios to `benchmark/scenarios.py`, held-out only (never added to
`CALIBRATION_SCENARIO_IDS`):**

- **Scenario 12 — Re-Quarantine Relapse.**
  ```
  Phase 1: 40 events,  success_rate=1.0,                         nominal baseline
  Phase 2: 100 events, success_rate=0.0, timed_out_rate=0.9       outage -> QUARANTINED
  Phase 3: 60 events,  success_rate=1.0, manual_restore=True      admin restore -> RECOVERING probe
  Phase 4: 50 events,  success_rate=0.0, timed_out_rate=0.9       relapse during RECOVERING -> re-QUARANTINE
  Phase 5: 60 events,  success_rate=1.0, manual_restore=True      genuine final recovery
  ```
- **Scenario 13 — Rapid Micro-Flapping.**
  ```
  8 repetitions of: 15 events @ success_rate=1.0, then 15 events @ success_rate alternating
  between 0.10 and 0.15 (timed_out_rate=0.3, http_5xx_rate=0.3 on the low-success blocks)
  Final phase: 60 events @ success_rate=1.0, manual_restore=True
  ```
- **Scenario 14 — Long Sustained Mild Degradation.**
  ```
  Phase 1: 40 events,  success_rate=1.0,                                    nominal
  Phase 2: 400 events, success_rate=0.75, latency_ms=700, timed_out_rate=0.1,
           http_5xx_rate=0.1                                                sustained mild degradation
  Phase 3: 60 events,  success_rate=1.0, manual_restore=True                recovery
  ```

**Wiring:**
- Add to `ALL_SCENARIOS`/`get_all_scenarios()` (IDs 12, 13, 14) and to `EVALUATION_SCENARIO_IDS`
  (now `[3,4,5,9,10,12,13,14]`). Do **not** add to `CALIBRATION_SCENARIO_IDS`.
- Extend Hypothesis 1's `outage_ids` in `benchmark/run_experiments.py` to `[3,4,9,10,12,14]`.
- Extend Hypothesis 2's flapping check to also verify Scenario 13 stays within a defined bound
  (e.g. `<= 10`), reported alongside the existing Scenario 5 check.
- Add an explicit assertion that Scenario 12 produces two separate `QUARANTINED` entries in its
  transition sequence (the actual new behavior being tested).
- Re-run `benchmark/run_experiments.py`, transcribe real output into
  `docs/report/04-evaluation-methodology.md` and `docs/research.md`. Transcribe real numbers only —
  never hand-edit them, and report the real reduction percentage even if it's a different story.

---

## 2. Free documentation fixes

- Name the common-random-numbers variance-reduction technique (same seed's telemetry reused across
  A/B/C within a run) explicitly in §4.8.1 of the report — one sentence.
- Note in §4.14.6 that Scenario 2 (High Latency) produces 0 retries for all three strategies and so
  doesn't contribute to the retry-reduction story — stated explicitly rather than left for a reader to
  notice.

---

## 3. Adopt genetic-optimizer-discovered weights as production

**Why:** `benchmark/genetic_optimizer.py` exists, does a real search over the calibration set, and
currently nothing uses its output. The user wants the discovered weights to actually become
`OPTIMAL_ERI_WEIGHTS` if they're good — not just reported alongside the hand-picked sweep.

**Steps:**
1. Run `benchmark/genetic_optimizer.py` against the calibration set `{1,2,6,7,8,11}` (after §1's
   scenario additions — calibration set is unaffected by those, so this can run independently).
   Use enough generations/population to actually converge (e.g. `--generations 30 --population 40
   --seed 42`; try 2-3 seeds and confirm convergence is stable, not seed-dependent).
2. **Acceptance bar** (same as the existing hand-picked sweep in `optimize_weights.py` — do not lower
   it to force adoption): zero false quarantines on `should_never_quarantine` scenarios, bounded
   flapping (`max_transitions <= 5` on the calibration set), zero quarantine-retries-wasted.
3. If the GA winner clears the bar:
   - Replace `OPTIMAL_ERI_WEIGHTS` in `relay/kernel/value_objects/eri.py` with the GA-discovered
     values. Update the docstring on `ERIWeights.optimal()` to describe it as genetic-algorithm-
     discovered over the calibration set (cite generations/population/seed), not the hand-picked
     "Hysteresis-Balanced" sweep.
   - Update `tests/unit/test_eri.py` (currently asserts `opt.success_rate_weight == 0.30` and likely
     other specific values) to the new numbers.
   - Grep for any other hardcoded reference to the old weight values (`0.30`, `0.15`, `0.20`, `0.08`,
     `0.02`, `0.10` in context) across `tests/` and `relay/control_plane/aede/service.py`'s defaults,
     and update.
   - **Re-run the entire downstream evaluation with the new weights** — `run_experiments.py`,
     `sensitivity_analysis.py`, `multi_endpoint.py` — since Strategy C's behavior changes whenever
     weights change. Every number in `docs/report/04-evaluation-methodology.md` from §4.9 onward
     (§4.14.1–4.14.7) needs to be regenerated from real output, not adjusted by hand. Same rule as
     ever: if the new weights produce a *weaker* headline number than the hand-picked profile, report
     that honestly — the point is that the weights were actually searched for, not that the number
     goes up.
   - Keep `optimize_weights.py`'s 4-candidate sweep in the report as the baseline the GA is compared
     against ("GA search found X, an improvement of Y% in fitness over the best hand-picked candidate,
     Z% in wasted retries over Hysteresis-Balanced") — this is useful context, not dead weight.
4. If the GA winner does **not** clear the bar (e.g., converges to a false-quarantining or flapping
   config), do not adopt it — keep `Hysteresis-Balanced`, but report the GA attempt and why it wasn't
   adopted. Don't force acceptance by loosening the bar.

---

## 4. Leave-one-out cross-validation within the calibration set

Six scenarios, all calibration-side — zero leakage risk. For each of `{1,2,6,7,8,11}`, re-run the
weight search (whichever won in §3 — hand-picked sweep or GA) excluding that one scenario, confirm the
winner/weights are stable across folds. Add as `benchmark/loo_calibration_check.py` or a function in
`benchmark/optimize_weights.py`; report result in §4.10.1.

---

## 5. Ablation, oracle baseline, and harness-vs-real-service validation

### 5a. Ablation study

Add two benchmark-only strategy variants (do not touch production `relay/control_plane/aede/` code):
- **Strategy C′ (no isolation):** full state machine + ERI, isolation action forced to `CONTINUE`.
- **Strategy C″ (no hysteresis):** full state machine with symmetric thresholds instead of the real
  asymmetric ones (`StateMachineThresholds` already takes these as constructor params).

Run both across the (expanded) held-out set, same 5-seed protocol. New §4.14.x "Component Ablation"
reporting wasted retries and transitions for A / B / C″ / C′ / C.

### 5b. Oracle upper bound

Benchmark-only "Strategy O" with cheating access to `ScenarioPhase.success_rate` (zero retries when
`success_rate == 0.0`, full retries otherwise). Report alongside A/B/C in the held-out comparison,
clearly labeled as a non-implementable upper bound.

### 5c. In-process validation of the benchmark harness against real `AEDEService`

New script `benchmark/validate_against_real_aede.py`: feed the same seeded `Telemetry` sequence (one
or two held-out scenarios) into a real, in-process `relay.control_plane.aede.service.AEDEService()`
via its actual `record_telemetry()` method, and compare its ERI/state/policy trajectory against what
`BenchmarkRunner` produces directly. No Kafka/containers needed — pure in-process Python. Report
agreement (or a genuine mismatch, which would itself be a real finding) in §4.10.4.

---

## 6. Wire the live dashboard to real delivery, then fix the demo for real

**Why:** the user wants `demo/run_demo.py` to actually work against real infrastructure, not just be
honest about not doing so. This is now in scope. Investigation of the real codebase found the
following, which makes this concretely buildable without inventing new architecture:

- The dashboard's Chart Data API (`relay/api/chart/router.py`, endpoints `/api/chart/endpoints/{id}/state`,
  `/eri-history`, `/policy`, `/explainability`) is fully built and correctly shaped — it reads from
  `EndpointStateStore` (`relay/api/chart/stores.py`), whose `record_evaluation(endpoint_id, state,
  eri_score, policy_evaluation, delivery_policy, explainability_record)` method is fully implemented
  but **currently called only from a unit test** — nothing in the live path calls it.
- This chart router is mounted in `relay/dashboard/main.py` (`app.include_router(chart_router)`) —
  a **third FastAPI app**, separate from `relay/api/main.py` (ingestion) and the `dispatcher` process.
  **`docker-compose.yml` does not currently run this dashboard app at all** — no `dashboard` service
  exists in it today.
- The real feedback loop runs inside the `dispatcher` container: `WebhookWorker` calls
  `self._aede_service.record_telemetry(observation)` at `relay/data_plane/workers/webhook.py:170`,
  which internally computes ERI, transitions state, synthesizes the policy, and persists an
  `ExplainabilityRecord` — but never constructs a `PolicyEvaluation` entity (defined in
  `relay/kernel/entities/policy_evaluation.py`, never instantiated anywhere in the codebase) and never
  tells anything outside its own process that an evaluation happened.
- A Kafka topic for exactly this purpose already exists, is already bootstrapped at startup, and is
  currently **completely unused**: `EVENTS_INTERNAL_POLICY_TOPIC = "events.internal.policy"` in
  `relay/infrastructure/messaging/admin.py`, part of `PLATFORM_TOPICS`. This matches
  `architecture.md`'s own documented "Policy Events" category (`PolicyEvaluated`, `PolicyUpdated`) —
  the intended mechanism was already designed, just never implemented.
- The existing `publish(topic, key, value)` helper (`relay/infrastructure/messaging/producer.py:60`)
  and `KafkaConsumer` wrapper with `async def messages() -> AsyncIterator[KafkaMessage]`
  (`relay/infrastructure/messaging/consumer.py`) are the only sanctioned way to touch Kafka (ADR 0001,
  ADR 0003) — reuse both, following the exact pattern `relay/data_plane/dispatcher/main.py` already
  uses for its own consumer loop, and the exact pattern `relay/api/endpoints.py`'s `restore_endpoint`
  already uses for its own producer call.

**Implementation:**

1. **`relay/control_plane/aede/service.py` — construct and publish an evaluation event.** In
   `record_telemetry()`, after synthesizing the policy and explainability record, construct a
   `PolicyEvaluation` (endpoint_id, policy_id, explainability_record_id, plus whatever else the entity
   requires — check `relay/kernel/entities/policy_evaluation.py` for the full field list). Serialize a
   JSON bundle containing everything `EndpointStateStore.record_evaluation()` needs (endpoint_id, state
   value, full `ERIScore` fields, the `PolicyEvaluation`, the `DeliveryPolicy`, the
   `ExplainabilityRecord`) and `await publish(EVENTS_INTERNAL_POLICY_TOPIC, key=endpoint_id,
   value=payload)`. Wrap in the same try/except-and-log pattern already used around the
   `record_telemetry` call in `webhook.py` — a publish failure must never break delivery.

2. **`relay/dashboard/main.py` — consume it.** Add a `lifespan` (matching `relay/api/main.py`'s
   `@asynccontextmanager` pattern) that starts a background `asyncio.Task` running a `KafkaConsumer`
   loop over `EVENTS_INTERNAL_POLICY_TOPIC`, deserializing each message and calling
   `state_store.record_evaluation(...)` (the same `state_store` singleton `chart_router` already reads
   from — import it from `relay.api.chart.stores`). Cancel the task on shutdown, following
   `close_producer()`'s cleanup pattern.

3. **`docker-compose.yml` — add the `dashboard` service.** New service running
   `uvicorn relay.dashboard.main:app --host 0.0.0.0 --port <pick one, e.g. 8090>`, same build context
   as `api`/`dispatcher`, depends on `kafka` being healthy, exposes its port to the host so
   `demo/run_demo.py` can reach it.

4. **`demo/run_demo.py` — replace every hardcoded value with a real read.** Add `--dashboard-url`
   (default `http://localhost:8090`, env override `DASHBOARD_URL`). Replace:
   - Every `self.current_state = "..."` assignment in `run()` with a poll of
     `GET {dashboard_url}/api/chart/endpoints/{endpoint_id}/state`, retrying with a short interval
     (e.g. every 0.5s) up to a bounded timeout (e.g. 30s) waiting for the expected state to appear —
     and **fail the phase loudly with a clear error if it never arrives**, instead of silently keeping
     the old hardcoded value. This is the actual point of doing this for real: it can now genuinely
     fail if the real system doesn't behave as expected.
   - Every `self.current_eri = ...` assignment with the score from
     `GET {dashboard_url}/api/chart/endpoints/{endpoint_id}/eri-history` (latest point).
   - The scripted `explainability_log.append({...})` calls with real records from
     `GET {dashboard_url}/api/chart/endpoints/{endpoint_id}/explainability`.
   - The policy label (`_get_policy_label()`) with real data from
     `GET {dashboard_url}/api/chart/endpoints/{endpoint_id}/policy`.
   - Remove section 6 ("Multi-Seed Empirical Research Benchmark Numbers") from the generated report —
     it's numbers from a different experiment regardless of the wiring fix. Replace with a summary
     computed from what this run actually measured (event counts, wall-clock time to quarantine, wall-
     clock recovery time), or a one-line pointer to `docs/report/04-evaluation-methodology.md` §4.14.

---

## Verification

1. `pytest tests/unit/test_benchmark.py tests/unit/test_genetic_optimizer.py tests/unit/test_eri.py -v`
   — all green after scenario additions, weight adoption, and ablation strategies land.
2. `python benchmark/run_experiments.py`, `genetic_optimizer.py`, `sensitivity_analysis.py`,
   `multi_endpoint.py`, the new ablation/oracle/validation scripts — run each, transcribe real output
   into the docs. Confirm Scenario 12's real transition sequence contains two `QUARANTINED` entries.
3. `docker compose up -d` (adds the new `dashboard` service), run `python demo/run_demo.py --quick`,
   confirm `demo/LIVE_REPORT.md` now shows state/ERI/explainability values that came from real
   `GET /api/chart/...` calls — spot check by comparing a couple of values against
   `docker compose logs dispatcher` around the same timestamp.
4. Full `pytest -q` for the repo — confirm nothing outside the touched files broke (particularly
   anything depending on the old `OPTIMAL_ERI_WEIGHTS` values, if §3 adopts new ones).
5. Standing rule: never tune a scenario, threshold, or weight to make a number look better, and never
   fake success in the demo's polling loop — a real failure to reach the expected state within the
   timeout is a real finding, report it rather than hiding it.
