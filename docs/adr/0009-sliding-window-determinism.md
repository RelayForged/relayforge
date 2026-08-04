# ADR 0009: Observation Window Design and the Determinism/Time Conflict

## Status

Accepted

## Context

The ERI (Endpoint Reliability Index) is computed over a sliding observation window. Two natural window definitions exist:

**Attempt-count-based window**: "Last N delivery attempts" — e.g., the most recent 100 attempts.

**Time-based window**: "Last T minutes of activity" — e.g., the last 5 minutes.

AEDE has a hard determinism requirement: identical inputs must always produce identical outputs. This requirement exists to enable reproducible experiments — two runs with the same telemetry dataset must produce the same policy decisions.

These two requirements are in direct conflict when using a time-based window.

### The Conflict

If ERI is computed over "the last 5 minutes" and two policy evaluations happen with slightly different wall-clock times (even milliseconds apart), the set of telemetry records included in the window differs. Two evaluations of "the same endpoint" with "the same telemetry history" produce different ERI scores purely because wall-clock time advanced between the evaluations.

This means:
- A policy decision made at T=10:00:00.000 may differ from one made at T=10:00:00.500 even if no new telemetry arrived.
- Re-running the same experiment at a later wall-clock time produces different policy decisions.
- Unit tests for AEDE that freeze the telemetry set may pass or fail depending on when they run.

This is not a theoretical concern — it directly undermines the reproducibility guarantee that the research evaluation depends on.

### Why Time-Based Windows Are Appealing

Time-based windows have a legitimate use case: they adapt to endpoints with low delivery volume. An endpoint that handles 5 deliveries per hour looks very different from one that handles 500 deliveries per minute. An attempt-count window of 100 attempts represents 20 hours of history for the slow endpoint but 12 seconds for the fast one. Using the same count-based window for both produces very different ERI responsiveness characteristics.

This is the real tension: count-based windows provide determinism; time-based windows provide volume-adaptivity.

## Decision

**MVP: use attempt-count-based windows only. No time-based filtering.**

```
ERI observation set = most recent N delivery attempts (default: 100)
```

The age filter is removed from the MVP implementation entirely. It is noted as a post-MVP enhancement. This is the simpler, fully deterministic choice.

The tradeoff — that count-based windows respond differently for high vs low-volume endpoints — is accepted for MVP. The benchmark experiments use controlled event volumes, so this asymmetry does not affect experimental validity. In production use, an operator can tune `N` per endpoint if needed.

**Critical implementation rule**: The time boundary must be passed as an explicit parameter to the ERI computation function — never read from wall-clock inside AEDE. The calling code stamps the boundary at the moment of invocation. This preserves determinism: replaying the same call with the same boundary timestamp produces the same result.

```python
# Correct — time boundary is an explicit parameter
eri = compute_eri(
    telemetry=records,
    window_size=100,
    window_cutoff=datetime(2024, 1, 15, 10, 0, 0)  # explicit, from caller
)

# Wrong — reads wall-clock inside the function
def compute_eri(telemetry):
    cutoff = datetime.now() - timedelta(minutes=5)  # non-deterministic
    ...
```

This pattern means:
- In production, the Dispatcher passes `datetime.now()` as the cutoff when requesting a policy evaluation.
- In tests and benchmarks, the cutoff is set to a fixed timestamp, making every run deterministic.
- In replay experiments, the cutoff is set to the original event's timestamp, meaning the ERI reflects what the system knew at that point in time.

### Documented Non-Determinism

One residual non-determinism remains: two production policy evaluations of the same endpoint at different wall-clock times will include different telemetry records if new delivery attempts have occurred between the evaluations. This is expected and correct behavior — it reflects actual new information about the endpoint. This is not the same as the spurious non-determinism described above (different results from the same telemetry due to wall-clock variance in window boundaries).

For the research evaluation, experiments use fixed event datasets replayed from Kafka. The cutoff timestamp is controlled by the benchmark harness. ERI computation is therefore fully deterministic across experiment runs.

## Consequences

**Positive:**
- ERI computation is a pure function of its inputs. Unit tests are stable regardless of when they run.
- Benchmark experiments are fully reproducible: the same dataset with the same configuration always produces the same policy decisions.
- The time cutoff as an explicit parameter also enables historical policy replay — "what would AEDE have decided at T=2024-01-15 10:00:00?" becomes computable.

**Negative:**
- The calling code is responsible for providing the cutoff timestamp. This is a leaky abstraction — the caller must know to provide this parameter correctly.
- Low-volume endpoints may have their age-limited window contain very few attempts, making ERI less stable. This is the inherent tradeoff of count-based windows for infrequent endpoints and should be noted in the evaluation.

**Mitigations:**
- Document the convention clearly: AEDE never reads wall-clock time internally. Tests that omit the cutoff parameter should fail at compile/lint time via type enforcement.
- For very low-volume endpoints (few attempts in the window), the ERI confidence is low. Future work could expose an ERI confidence score alongside the ERI value to reflect window sparsity.

This decision must be enforced at code review. Any AEDE implementation that reads `datetime.now()` or `time.time()` internally (other than for logging) is a violation of this ADR.
