# ADR 0027: Timed Quarantine with Automatic Probing and Replay

## Status

Accepted (Person B, 2026-10-15)

**Amends:** ADR 0012 (QUARANTINED as a sink state), ADR 0020

## Context

ADR 0012 made `QUARANTINED` a state that only an operator restore can leave. Quarantine therefore cost a human: an endpoint that failed briefly at night stayed cut off, with its events held, until someone noticed. `config.yaml`, the architecture document and the report all define `quarantine_duration_seconds: 300`, and `config_loader.py` loads it, but nothing used it. The timed quarantine was designed and never built.

Waiting longer before quarantining would not fix this. AEDE's measured advantage comes from stopping early (fewer wasted retries, freed workers), and a later quarantine still needs a human to end it.

## Decision

Quarantine is a timed hold, like a circuit breaker's open state.

1. **Hold.** On entering `QUARANTINED`, AEDE records the time. After `AEDE_QUARANTINE_DURATION_SECONDS` (default 300) the hold expires.
2. **Probe.** On the next delivery for that endpoint, the Dispatcher calls `AEDEService.check_quarantine_hold(endpoint_id, now)`, passing the time in; AEDE never reads the clock. An expired hold moves the endpoint to `RECOVERING` exactly as an operator restore does: window reset (ADR 0020), probation (ADR 0023), Explainability Record ("Quarantine hold of N s expired …"), policy event.
3. **Back off.** If the probes fail and the endpoint is quarantined again from `RECOVERING`, the next hold doubles, up to `AEDE_QUARANTINE_MAX_DURATION_SECONDS` (default 3600). A successful recovery resets it. The rule is the pure function `quarantine_hold_seconds()` in `relay/control_plane/state_machine/quarantine.py`.
4. **Replay.** When an endpoint goes `RECOVERING → AVAILABLE`, AEDE notifies a registered listener. With `RELAY_AUTO_REPLAY=true`, the Dispatcher's listener runs the ReplayManager for that endpoint (ADR 0021), so held events are resent without an operator. AEDE only announces; the Data Plane acts.

`AEDE_QUARANTINE_DURATION_SECONDS=0` disables the timed hold and restores the old behaviour. Operator restore remains the fast path. Compose uses a 30 s hold, a 240 s cap and auto-replay on, so the behaviour is visible in a demo.

## Effect on the benchmark

`BenchmarkRunner` takes `quarantine_hold_seconds` (default 0, the published model; one simulated event is one second). `benchmark/timed_quarantine_check.py` compares Strategy C over all scenarios × 5 seeds:

| Held-out, 8 scenarios × 5 seeds | Wasted retries | vs A | vs B | Success |
|---|---|---|---|---|
| A | 45,260 | | | 0.5855 |
| B | 27,773 | | | 0.5855 |
| C, hold off (published) | 8,943 | −80.2% | −67.8% | 0.5855 |
| C, hold 300 s (default) | 8,943 | −80.2% | −67.8% | 0.5855 |
| C, hold 120 s | 9,634 | −78.7% | −65.3% | 0.5855 |
| C, hold 30 s | 11,095 | −75.5% | −60.1% | 0.5855 |

At the configured 300 s every held-out scenario gives exactly the published numbers, because no scenario's outage outlasts the hold before its scripted restore. Shorter holds probe failing endpoints more often and cost some retries, and C still beats both baselines by a wide margin. Success rate never changes.

## Consequences

- No quarantine needs a human to end it. Verified on the compose stack: a merchant's server was stopped, quarantined, restarted, probed after 30 s, promoted after probation, and every held payment was delivered once by automatic replay, with no `recover` call.
- A quarantine is only probed when a delivery for that endpoint arrives. An endpoint with no traffic stays quarantined, which costs nothing because there is nothing to deliver. Its held events are replayed the next time it recovers.
- Hold timers and relapse counts live in the Dispatcher's memory, like all AEDE state, and are lost on restart.
- Retries already scheduled before a quarantine still execute when due (the Dispatcher does not re-check isolation for scheduled retries). That is a separate gap.
