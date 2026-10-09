# Fix research-contribution integrity issues in the AEDE benchmark suite

## Context

A prior audit of this branch's research contribution (AEDE benchmark suite, `docs/research.md`,
`docs/report/04-evaluation-methodology.md`) found two real problems:

1. **Weight-selection leakage.** `benchmark/optimize_weights.py` and `benchmark/genetic_optimizer.py`
   pick/validate the production ERI weight profile (`OPTIMAL_ERI_WEIGHTS` in
   `relay/kernel/value_objects/eri.py`) by scoring fitness **on the same 10 scenarios**
   (`benchmark/scenarios.py`) that `benchmark/run_experiments.py` then uses to report the headline
   "Strategy C beats A/B by X%" claim and to validate Hypotheses 1–3. This is training on the test set.
2. **Report/code drift.** Running the actual committed scripts produces different numbers than what's
   published in `docs/report/04-evaluation-methodology.md` (confirmed by actually running them — see
   "Grounding data" below). Section 4.14.7 (multi-endpoint contention) is the worst offender: the script
   doesn't even compute the "Head-of-Line Blocking Index" column the chapter cites, and its own
   hardcoded narrative string ("preserving <1ms queue wait") contradicts the real numbers printed right
   next to it.

Decision: fix #1 with a **fixed calibration/held-out scenario split** (not leave-one-out CV — simpler,
and it already lines up with the existing Hypothesis 1/2/3 scenario choices). Fix #2 by **making the
chapter match the script's real output**, not by rebuilding the script to hit numbers the chapter
already claims (code → report, never the reverse).

This plan is written for a different agent to execute — it must be self-contained. That agent should
NOT need this conversation's history to act on it.

## Grounding data (already verified by actually running the code, not assumed)

- **Calibration/held-out split does not change the production winner.** Re-scoring the existing 4
  candidate profiles in `CANDIDATE_PROFILES` (`benchmark/optimize_weights.py`) using *only* scenarios
  `{1, 2, 6, 7, 8, 11}` (excluding the scenarios Hypotheses 1–3 use) still picks **Hysteresis-Balanced**
  as the winner:
  ```
  Rank Candidate             Score   FalseQ  Det(11) Quar(7) MaxTrans TotalRetr QuarLeak
  1    Hysteresis-Balanced   1870.0  False   29      180     4        2024      0
  2    Baseline Default      1762.5  False   36      180     3        2211      0
  3    Failure-Aggressive    1641.5  False   39      180     6        2141      0
  4    Latency-Sensitive     1617.0  False   40      180     3        2486      0
  ```
  (Computed via a scratch reimplementation of the fitness function restricted to scenarios
  `{1,2,6,7,8,11}`, using Scenario 11 as the early-detection proxy and Scenario 7 as the
  quarantine-speed proxy — see step 2 below for how to build this into the real file.)
  → **`OPTIMAL_ERI_WEIGHTS` in `relay/kernel/value_objects/eri.py` does not need to change.** This
  keeps the blast radius small — `relay/control_plane/aede/service.py`, `tests/unit/test_eri.py`, and
  the integration tests that reference `ERIWeights.optimal()` do not need updating for the weight
  values themselves.
- **Strategy A's real outage retry total (scenarios 3,4,9,10, seed 42) is 4,010** (600 + 1500 + 1000 +
  910), not the `4000.0` currently hardcoded in `benchmark/sensitivity_analysis.py` as
  `static_outage_retries`. Close by coincidence, but it's a guess, not a computed value, and it varies
  by seed/scenario — it must be computed live.
- **Running `benchmark/run_experiments.py` today** (before any fix) produces: Strategy A = 7,906 ± 148.4
  wasted retries, 2,310 total events, 65.8% success rate, H1 outage reduction = **53.3%** — vs. the
  chapter's published 7,858.0 ± 78.5 / 2,190 events / 65.3% / 67.0%. None of these match currently.
  **53.3% still clears the ≥50% bar, but narrowly** — worth flagging below.
- **Running `benchmark/multi_endpoint.py` today** (defaults: 4 workers, 200 events/tenant) produces a
  real table (Strategy A/B/C: 1,600/750/418 Tenant-1 retries; 52,704.8/39,277.3/27,592.1 ms Tenant-2 avg
  queue wait). The script's own printed "Key Empirical Finding" claims "<1ms queue wait" for Strategy C
  — false, contradicted by the number two lines above it. The chapter's Table 4.7 (10 workers, 1000
  events, "Head-of-Line Blocking Index" column) does not correspond to anything the script computes.
- Nothing outside `benchmark/` imports `get_all_scenarios`/`ALL_SCENARIOS`, so adding new scenario
  groupings inside `benchmark/scenarios.py` is safe and self-contained.
- Docs currently say "10 failure scenarios" everywhere, but the code has 11 (`Scenario 11: Bursty
  Traffic` was added later, purely as a sliding-window-reactivity check). Scenario 11 should be treated
  as calibration-only and explicitly called out as supplementary to the original 10-scenario taxonomy,
  not folded into the "10 scenarios" language — this resolves that drift too.

## The split

- **Calibration set** (used only by `optimize_weights.py` / `genetic_optimizer.py`):
  `{1, 2, 6, 7, 8, 11}` — Intermittent Timeouts, High Latency, Credential Failure, Connection Failure,
  Rate Limiting, Bursty Traffic. Includes both "must never quarantine" scenarios (1, 2) and a mix of
  persistent/partial failure types.
- **Held-out evaluation set** (used only for the Strategy A/B/C headline comparison and Hypotheses 1–3):
  `{3, 4, 5, 9, 10}` — Burst Failures, Endpoint Outage, Partial Recovery, Network Partition, Slow
  Degradation. This already exactly matches what `run_experiments.py`'s H1 (`outage_ids = [3,4,9,10]`),
  H2 (scenario 5), and H3 (scenario 4) use today — so the hypothesis-testing code itself needs almost no
  change, only the calibration code does.

## Implementation steps

### 1. `benchmark/scenarios.py`
- Add `should_never_quarantine: bool = False` field to the `Scenario` dataclass; set `True` on
  `SCENARIO_1_INTERMITTENT_TIMEOUTS` and `SCENARIO_2_HIGH_LATENCY`.
- Add `CALIBRATION_SCENARIO_IDS = [1, 2, 6, 7, 8, 11]`, `EVALUATION_SCENARIO_IDS = [3, 4, 5, 9, 10]`, and
  `get_calibration_scenarios()` / `get_evaluation_scenarios()` helper functions alongside the existing
  `get_all_scenarios()` (leave `get_all_scenarios()` and `ALL_SCENARIOS` untouched — other code may rely
  on the full 11).

### 2. `benchmark/optimize_weights.py` (the actual leakage fix)
- `evaluate_candidate()` currently hardcodes `results_by_id[1]`, `[2]`, `[4]`, `[5]`, `[10]` — i.e. it
  reaches directly into held-out scenarios. Rewrite it to:
  - Default `scenarios` param to `get_calibration_scenarios()` instead of `get_all_scenarios()`.
  - Replace the false-quarantine check with a generic one driven by `sc.should_never_quarantine` instead
    of hardcoded IDs 1/2 (works for whatever `scenarios` were actually passed in).
  - Replace the `scen10_detection_event` bonus with a generic "early detection" bonus computed on
    **Scenario 11** (bursty traffic; degradation onset ~event 20) instead of Scenario 10.
  - Replace the `scen4_quarantine_event` bonus with a generic "quarantine speed under persistent
    failure" bonus computed on **Scenario 7** (connection failure; persistent near-total failure)
    instead of Scenario 4.
  - Replace the hardcoded `scen5_transitions` flapping check with a generic
    "max transitions across any scenario actually evaluated" check (still penalize `> 5`).
  - Total-retries and quarantine-retries-wasted terms already sum across `results_by_id` generically —
    just make sure they iterate over whatever scenario set was passed, not a hardcoded list.
  - `CandidateEvaluation` fields that named specific scenarios (`scen10_detection_event`,
    `scen4_quarantine_event`, `scen5_transitions`) should be renamed to reflect what they now measure
    (e.g. `detection_event`, `quarantine_event`, `max_transitions`) — update `print_optimization_report`
    and the two call sites in `benchmark/sensitivity_analysis.py` and `tests/unit/test_benchmark.py`
    accordingly.
  - Keep the acceptance assertions at the bottom of `__main__` (false-quarantine, flapping, zero
    quarantine leak), just updated to the renamed fields.
- Re-run the script after the rewrite and confirm it still reports **Hysteresis-Balanced** as the
  winner (expected, per the grounding data above — if it picks something else, stop and surface this
  before touching `eri.py`, since that would ripple into production defaults and several tests).

### 3. `benchmark/genetic_optimizer.py`
- Change the default `scenarios` argument (in `evaluate_weights`, `setup_toolbox`,
  `run_genetic_optimizer`) from `get_all_scenarios()` to `get_calibration_scenarios()`.
- Its false-quarantine check already uses `sc.scenario_id in (1, 2)` — fine as is, or switch to
  `sc.should_never_quarantine` for consistency with step 2.
- `tests/unit/test_genetic_optimizer.py` passes explicit `scenarios=[sc1]` in its tests, so it's
  unaffected by the default change — just confirm it still passes.

### 4. `benchmark/sensitivity_analysis.py`
- Delete the hardcoded `static_outage_retries = 4000.0` constant. Replace it with an actual
  `BenchmarkRunner(strategy_config=DeliveryStrategyConfig(strategy="STRATEGY_A"))` run over
  `outage_ids = [3, 4, 9, 10]` (same seed as the rest of the analysis), summing real `retry_count`
  across attempts, exactly like the probe computation in "Grounding data" above. This file does not
  need to change which scenarios it reads (1, 2, 5, 9, 10, etc.) — it's a post-hoc robustness diagnostic
  on the one already-chosen production config, not a selection mechanism, so it's fine for it to look at
  held-out scenarios. Only the hardcoded magic number is the bug.
- Update any field renames flowing from step 2 (`CandidateEvaluation`/`evaluate_candidate` signature).

### 5. `benchmark/multi_endpoint.py` — match the chapter to reality, don't rebuild
- Leave `run_contention_simulation()`'s defaults as-is (4 workers, 200 events/tenant, seed 42).
- Fix `generate_multi_endpoint_table()`'s hardcoded `"> [!IMPORTANT]"` narrative block: it currently
  asserts "preserving low latency (< 1ms queue wait)" unconditionally. Replace with text derived from
  the actual computed numbers (e.g. report the real percentage reduction in Tenant 2's avg queue wait
  for Strategy C vs Strategy A, computed in the function, not a fixed string). Do the same for the
  "Noisy Neighbor Impact" column — replace the three hardcoded qualitative labels
  (`SEVERE`/`MODERATE`/`ZERO`) with labels or numbers actually derived from each run's computed queue
  wait, so the label can never contradict the data next to it again.
- No new "Head-of-Line Blocking Index" metric — it doesn't exist today and inventing one now would
  repeat the original mistake (report claims a metric, code must then be bent to produce it). If a
  simple, honest derived metric is easy to add (e.g.
  `1 - healthy_avg_wait_under_test / healthy_avg_wait_strategy_A` as a normalized "relative protection"
  ratio), it's fine to add it as long as it's computed, not asserted, and the report text is written
  from whatever it actually comes out to.

### 6. `relay/kernel/value_objects/eri.py`
- Update the docstring on `ERIWeights.optimal()` / the comment above `OPTIMAL_ERI_WEIGHTS`. Currently:
  "Empirically calibrated optimal weights selected by benchmark/optimize_weights.py... Provides...".
  Replace the word "optimal" framing with something that matches `docs/research.md`'s own disclaimer
  (weights are a disclosed, calibrated default, not claimed globally optimal) and explicitly name the
  calibration scenario set (`{1,2,6,7,8,11}`) so this docstring can't drift from the methodology again.

### 7. `docs/research.md`
- In "Secondary Contribution (Weight Sensitivity Analysis)": change from future-tense ("if time
  permits... should be included") to describing it as implemented, since `sensitivity_analysis.py`
  already exists and runs.
- Add one paragraph (in "Threats to Validity" → internal validity / weight sensitivity) explicitly
  stating the calibration/held-out split methodology: weights are calibrated only on scenarios
  `{1,2,6,7,8,11}`; the Strategy A/B/C comparison and Hypotheses 1–3 are evaluated only on
  `{3,4,5,9,10}`, which the weight calibration never sees.
- Note Scenario 11 as a later, supplementary addition (sliding-window/bursty-traffic reactivity check),
  not part of the original 10-scenario taxonomy, to resolve the "10 vs 11 scenarios" drift.

### 8. `docs/report/04-evaluation-methodology.md` — regenerate every number from real runs
After steps 1–5 are implemented, actually run each script and transcribe its real output — do not
hand-edit numbers:
- §4.6/4.6.1: add one paragraph naming the calibration set `{1,2,6,7,8,11}` and held-out evaluation set
  `{3,4,5,9,10}`, and note Scenario 11 as supplementary.
- §4.7.2 "Execution Loop" table citing `max_retries: 10 → 8 → 5 → 0 → 6`: verify against real runtime
  values or regenerate from actual policy outputs — don't leave stale if it no longer matches.
- §4.9 (statistical results), §4.14.1 (weight optimization table — now run on the calibration set,
  using the renamed `detection_event`/`quarantine_event` fields and Scenario 11/7 as the reference
  columns instead of Scenario 10/4), §4.14.2 (overall comparison), §4.14.3 (Hypothesis 1 — label the
  table explicitly "held-out scenarios, never seen during calibration"), §4.14.4 (Hypothesis 2), §4.14.5
  (Hypothesis 3), §4.14.6 (detailed per-scenario table): replace every number with the real output of
  `python benchmark/run_experiments.py` after the fix.
  - **Important**: do not adjust scenario definitions, thresholds, or the `>= 50%` assertion in
    `run_experiments.py` to force a better-looking number. The grounding run above already shows
    ~53.3% reduction, which still clears the bar — report whatever the real post-fix number is, even if
    it's a less dramatic story than the current 67.0% claim. If a hypothesis assertion stops clearing
    its bar after the fix, that is itself a valid finding to report honestly, not something to
    re-tune away.
- §4.10.1 (sensitivity analysis table): regenerate from the fixed `sensitivity_analysis.py` (real
  `static_outage_retries`, not 4000.0).
- §4.14.7 (multi-endpoint): replace the whole table and narrative with the real output of
  `python benchmark/multi_endpoint.py` (4 workers / 200 events, as the script actually runs), dropping
  the non-existent "Head-of-Line Blocking Index" column unless step 5 added a real computed equivalent.
  Update the opening sentence ("10 concurrent workers... 1,000 concurrent events") to match the
  script's real parameters.
- Add a short limitation note (Construct Validity subsection, §4.10.3 or nearby) disclosing that
  "wasted retries" is computed as the active policy's configured `max_retries` at the moment of a
  failed synthetic attempt, not a full simulation of each retry independently succeeding or failing —
  this is a modeling simplification, not a bug, but it should be stated explicitly.
- Fix the `CANDIDATE_PROFILES` naming drift: the chapter currently says "Baseline Uniform" /
  "Latency-Heavy" where the code says `"Baseline Default"` / `"Latency-Sensitive"` — make the chapter
  use the code's actual names verbatim.

### 9. Tests
- `tests/unit/test_benchmark.py::test_optimize_weights_runs_and_identifies_winner`: still expects
  `winner_name == "Hysteresis-Balanced"` — should still pass per the grounding run, but re-verify after
  the real rewrite (field renames mean the assertions referencing `scen5_transitions` etc. need updating
  to the new field names).
- `tests/unit/test_benchmark.py::test_evaluate_candidate_detects_false_quarantine`: calls
  `evaluate_candidate("Bad Candidate", bad_weights)` with no `scenarios` arg — will now default to the
  calibration set, which still includes Scenario 2 (the one this test relies on to trigger a false
  quarantine) — should still pass unmodified.
- `tests/unit/test_benchmark.py::test_run_experiment_matrix_and_tables` and
  `test_sensitivity_analysis_execution_and_stability`: re-run after changes; update any field-name
  references.
- No change expected to `tests/unit/test_eri.py` (weights unchanged) or integration tests referencing
  `ERIWeights.optimal()`.

## Verification

1. `pytest tests/unit/test_benchmark.py tests/unit/test_genetic_optimizer.py tests/unit/test_eri.py -v`
   — all green.
2. `python benchmark/optimize_weights.py` — confirm winner and paste real output into §4.14.1.
3. `python benchmark/run_experiments.py` — confirm H1/H2/H3 assertions (report real pass/fail, don't
   force it), paste real output into §4.9, §4.14.2–4.14.6.
4. `python benchmark/sensitivity_analysis.py` — confirm `all_stable == True` using the real computed
   outage baseline, paste into §4.10.1.
5. `python benchmark/multi_endpoint.py` — confirm the printed narrative text numerically matches the
   table above it (no more self-contradiction), paste into §4.14.7.
6. Grep `docs/research.md` and `docs/report/04-evaluation-methodology.md` for any remaining numbers
   that weren't touched by steps 2–5 above (e.g. "10 failure scenarios" language, old candidate names)
   to catch anything missed.

## Notes for the executing agent

- Use the project venv: `./.venv/Scripts/python.exe` (Windows, PowerShell/Git Bash environment).
- Do not change `OPTIMAL_ERI_WEIGHTS`'s numeric values unless step 2's re-run genuinely picks a
  different winner than Hysteresis-Balanced — if so, stop and flag it rather than pushing the change
  through silently, since it ripples into `relay/control_plane/aede/service.py` and multiple test files.
- The entire point of this fix is integrity: never adjust code (scenarios, thresholds, weights) just to
  make a report number look better. If post-fix numbers are weaker than what's currently published,
  write the weaker, true numbers into the chapter.
