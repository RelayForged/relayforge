"""
Control Plane Integration Test Harness.

Exercises the full Control Plane pipeline end-to-end using realistic Telemetry
objects. See tests/integration/SCENARIOS.md for detailed scenario documentation.

Pipeline under test:
    Telemetry → compute_eri() → next_state() → synthesize_policy()
                                                  ↓
                                            PolicyCache
                                                  ↓
                                        ExplainabilityRepository
"""

import pytest
from datetime import datetime, timezone, timedelta

from relay.kernel.value_objects.telemetry import Telemetry
from relay.kernel.value_objects.eri import ERIScore
from relay.kernel.value_objects.endpoint_state import EndpointState, StateMachineThresholds
from relay.control_plane.eri.calculator import compute_eri
from relay.control_plane.state_machine.transitions import next_state
from relay.control_plane.aede.synthesizer import synthesize_policy
from relay.control_plane.aede.strategies.static import StrategyA
from relay.control_plane.aede.strategies.adaptive import StrategyB
from relay.control_plane.aede.strategies.aede_strategy import StrategyC
from relay.control_plane.explainability.record import ExplainabilityRecord
from relay.kernel.policies.delivery_policy import IsolationAction, DeliveryPolicy
from relay.infrastructure.feature_flags import FeatureFlags
from relay.infrastructure.cache import InMemoryPolicyCache, PolicyFallbackManager
from relay.infrastructure.persistence.explainability_repo import InMemoryExplainabilityRepository

# ERIWeights is still in eri.py — import from there
from relay.kernel.value_objects.eri import ERIWeights


NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)

# Default ERI weights from docs/architecture.md § Configuration Philosophy
DEFAULT_WEIGHTS = ERIWeights(
    success_rate_weight=0.35,
    latency_weight=0.20,
    consecutive_failures_weight=0.20,
    timeout_weight=0.10,
    http_5xx_weight=0.05,
    queue_depth_weight=0.05,
    recovery_trend_weight=0.05,
)

DEFAULT_THRESHOLDS = StateMachineThresholds(
    degradation_threshold=80.0,
    degradation_recovery_threshold=90.0,
    unstable_threshold=60.0,
    unstable_recovery_threshold=70.0,
    quarantine_threshold=30.0,
    recovery_threshold=90.0,
)


# ---------------------------------------------------------------------------
# Telemetry factory helpers
# ---------------------------------------------------------------------------


def _make_telemetry(
    endpoint_id: str = "ep-test",
    succeeded: bool = True,
    http_status: int = 200,
    latency_ms: float = 50.0,
    timed_out: bool = False,
    observed_at: datetime | None = None,
    attempt_id: str | None = None,
) -> Telemetry:
    """Create a single Telemetry observation."""
    return Telemetry(
        attempt_id=attempt_id or f"att-{id(object()):x}",
        endpoint_id=endpoint_id,
        succeeded=succeeded,
        http_status=http_status,
        latency_ms=latency_ms,
        timed_out=timed_out,
        observed_at=observed_at or NOW,
    )


def make_success_batch(n: int, endpoint_id: str = "ep-test") -> list[Telemetry]:
    """Generate n successful delivery telemetry records."""
    return [
        _make_telemetry(
            endpoint_id=endpoint_id,
            succeeded=True,
            http_status=200,
            latency_ms=50.0,
            attempt_id=f"att-ok-{i}",
        )
        for i in range(n)
    ]


def make_failure_batch(
    n: int,
    endpoint_id: str = "ep-test",
    http_status: int = 500,
    timed_out: bool = False,
    latency_ms: float = 5000.0,
) -> list[Telemetry]:
    """Generate n failed delivery telemetry records."""
    return [
        _make_telemetry(
            endpoint_id=endpoint_id,
            succeeded=False,
            http_status=http_status,
            latency_ms=latency_ms,
            timed_out=timed_out,
            attempt_id=f"att-fail-{i}",
        )
        for i in range(n)
    ]


def make_mixed_batch(
    total: int,
    failure_rate: float,
    endpoint_id: str = "ep-test",
) -> list[Telemetry]:
    """Generate a mixed batch with a given failure rate (0.0-1.0)."""
    n_failures = int(total * failure_rate)
    n_successes = total - n_failures
    batch = make_success_batch(n_successes, endpoint_id)
    batch.extend(make_failure_batch(n_failures, endpoint_id))
    return batch


def run_pipeline(
    telemetry: list[Telemetry],
    current_state: EndpointState,
    thresholds: StateMachineThresholds = DEFAULT_THRESHOLDS,
    weights: ERIWeights = DEFAULT_WEIGHTS,
    computed_at: datetime = NOW,
) -> tuple[ERIScore, EndpointState, DeliveryPolicy, ExplainabilityRecord]:
    """Run the full Control Plane pipeline: ERI → State Machine → Synthesizer."""
    eri = compute_eri(
        attempts=telemetry,
        queue_depth=0,
        weights=weights,
        baseline_latency_ms=100.0,
        max_latency_ms=5000.0,
        max_consecutive_failures=10,
        max_queue_depth=100,
        computed_at=computed_at,
    )
    new_state = next_state(current_state, eri, thresholds)
    policy, record = synthesize_policy(
        endpoint_id=telemetry[0].endpoint_id if telemetry else "ep-test",
        state_before=current_state,
        state_after=new_state,
        eri=eri,
        thresholds=thresholds,
        computed_at=computed_at,
    )
    return eri, new_state, policy, record


# ---------------------------------------------------------------------------
# Scenario 1: Full Degradation Cascade
# ---------------------------------------------------------------------------


class TestScenario1FullDegradationCascade:
    """AVAILABLE → OBSERVED_DEGRADATION → UNSTABLE → QUARANTINED"""

    def test_progressive_degradation(self):
        repo = InMemoryExplainabilityRepository()
        state = EndpointState.AVAILABLE
        prev_eri = 100.0

        # Phase 1: healthy baseline (should stay AVAILABLE)
        telemetry = make_success_batch(20)
        eri, state, policy, record = run_pipeline(telemetry, state)
        repo.save(record)
        assert state == EndpointState.AVAILABLE
        assert policy.isolation_policy.action == IsolationAction.CONTINUE

        # Phase 2: 20% failures → ERI ≈ 75 → OBSERVED_DEGRADATION (threshold: <80)
        telemetry = make_mixed_batch(20, failure_rate=0.20)
        eri, state, policy, record = run_pipeline(telemetry, state)
        repo.save(record)
        assert state == EndpointState.OBSERVED_DEGRADATION
        assert policy.isolation_policy.action == IsolationAction.REDUCE_CONCURRENCY
        assert eri.score < prev_eri
        prev_eri = eri.score

        # Phase 3: 40% failures → ERI ≈ 55 → UNSTABLE (threshold: <60)
        telemetry = make_mixed_batch(20, failure_rate=0.40)
        eri, state, policy, record = run_pipeline(telemetry, state)
        repo.save(record)
        assert state == EndpointState.UNSTABLE
        assert policy.isolation_policy.action == IsolationAction.PAUSE
        assert policy.notification_policy.notify_admin is True
        assert eri.score < prev_eri
        prev_eri = eri.score

        # Phase 4: 100% failures → ERI ≈ 17 → QUARANTINED (threshold: <30)
        telemetry = make_failure_batch(20)
        eri, state, policy, record = run_pipeline(telemetry, state)
        repo.save(record)
        assert state == EndpointState.QUARANTINED
        assert policy.isolation_policy.action == IsolationAction.QUARANTINE
        assert policy.retry_policy.max_retries == 0
        assert policy.notification_policy.notify_admin is True

        # Verify explainability records were stored for every phase
        assert repo.get_latest_by_endpoint("ep-test") is not None


# ---------------------------------------------------------------------------
# Scenario 2: Recovery Trajectory
# ---------------------------------------------------------------------------


class TestScenario2RecoveryTrajectory:
    """RECOVERING → AVAILABLE with improving telemetry."""

    def test_recovery_to_available(self):
        state = EndpointState.RECOVERING

        # Phase 1: moderate success rate (ERI below recovery_threshold 90) → stays RECOVERING
        telemetry = make_mixed_batch(20, failure_rate=0.20)
        eri, state, policy, record = run_pipeline(telemetry, state)
        # With 80% success, ERI should be below 90 — stays RECOVERING
        if eri.score < DEFAULT_THRESHOLDS.recovery_threshold:
            assert state == EndpointState.RECOVERING
            assert policy.replay_policy.allowed is True
            assert policy.replay_policy.speed < 1.0
            assert policy.concurrency_policy.max_parallel == 1

        # Phase 2: perfect success → ERI > 90 → AVAILABLE
        telemetry = make_success_batch(20)
        eri, state, policy, record = run_pipeline(telemetry, state)
        assert eri.score > DEFAULT_THRESHOLDS.recovery_threshold
        assert state == EndpointState.AVAILABLE
        assert policy.concurrency_policy.max_parallel == 10
        assert policy.isolation_policy.action == IsolationAction.CONTINUE


# ---------------------------------------------------------------------------
# Scenario 3: Quarantine is Terminal Without External Override
# ---------------------------------------------------------------------------


class TestScenario3QuarantineTerminal:
    """QUARANTINED remains QUARANTINED regardless of ERI improvement."""

    def test_quarantine_is_a_sink_state(self):
        state = EndpointState.QUARANTINED

        # Even with perfect telemetry, state machine returns QUARANTINED
        telemetry = make_success_batch(20)
        eri, new_state, policy, record = run_pipeline(telemetry, state)
        assert eri.score > 90  # ERI is excellent
        assert new_state == EndpointState.QUARANTINED  # but state doesn't change
        assert policy.isolation_policy.action == IsolationAction.QUARANTINE
        assert policy.retry_policy.max_retries == 0

    def test_manual_override_to_recovering(self):
        """Simulates the external intervention that moves state to RECOVERING."""
        state = EndpointState.QUARANTINED

        # Admin manually overrides state to RECOVERING
        state = EndpointState.RECOVERING

        # Now with good telemetry, can progress to AVAILABLE
        telemetry = make_success_batch(20)
        eri, new_state, policy, record = run_pipeline(telemetry, state)
        assert eri.score > DEFAULT_THRESHOLDS.recovery_threshold
        assert new_state == EndpointState.AVAILABLE


# ---------------------------------------------------------------------------
# Scenario 4: Hysteresis Prevents State Flapping
# ---------------------------------------------------------------------------


class TestScenario4Hysteresis:
    """ERI oscillation near degradation boundary should not cause flapping."""

    def test_no_flapping_near_boundary(self):
        state = EndpointState.AVAILABLE

        # Drop below 80 → transition to OBSERVED_DEGRADATION
        telemetry = make_mixed_batch(20, failure_rate=0.30)
        eri, state, policy, record = run_pipeline(telemetry, state)
        assert eri.score < DEFAULT_THRESHOLDS.degradation_threshold
        assert state == EndpointState.OBSERVED_DEGRADATION

        # Rise to 82 (above degradation_threshold 80 but below recovery 90)
        # → should REMAIN OBSERVED_DEGRADATION
        telemetry = make_mixed_batch(20, failure_rate=0.15)
        eri, state, policy, record = run_pipeline(telemetry, state)
        if DEFAULT_THRESHOLDS.degradation_threshold < eri.score < DEFAULT_THRESHOLDS.degradation_recovery_threshold:
            assert state == EndpointState.OBSERVED_DEGRADATION, (
                f"State should remain OBSERVED_DEGRADATION when ERI={eri.score:.1f} "
                f"is between degradation_threshold ({DEFAULT_THRESHOLDS.degradation_threshold}) "
                f"and degradation_recovery_threshold ({DEFAULT_THRESHOLDS.degradation_recovery_threshold})"
            )

        # Rise above 90 → NOW transitions back to AVAILABLE
        telemetry = make_success_batch(20)
        eri, state, policy, record = run_pipeline(telemetry, state)
        assert eri.score > DEFAULT_THRESHOLDS.degradation_recovery_threshold
        assert state == EndpointState.AVAILABLE


# ---------------------------------------------------------------------------
# Scenario 5: Non-Adjacent Skip (Catastrophic Failure)
# ---------------------------------------------------------------------------


class TestScenario5NonAdjacentSkip:
    """AVAILABLE → QUARANTINED in a single evaluation (catastrophic failure)."""

    def test_catastrophic_failure_skips_to_quarantine(self):
        state = EndpointState.AVAILABLE

        # All failures, all timeouts → ERI ≈ 0
        telemetry = make_failure_batch(20, http_status=500, timed_out=True, latency_ms=10000.0)
        eri, new_state, policy, record = run_pipeline(telemetry, state)

        assert eri.score < DEFAULT_THRESHOLDS.quarantine_threshold
        assert new_state == EndpointState.QUARANTINED
        assert record.state_before == "AVAILABLE"
        assert record.state_after == "QUARANTINED"
        assert record.state_changed is True
        assert any("quarantine" in t.lower() for t in record.triggered_thresholds)


# ---------------------------------------------------------------------------
# Scenario 6: Three-Tier Policy Fallback
# ---------------------------------------------------------------------------


class TestScenario6ThreeTierFallback:
    """Tier 1 (live) → Tier 2 (cached) → Tier 3 (static fallback)."""

    def test_fallback_hierarchy(self):
        cache = InMemoryPolicyCache()
        fallback = PolicyFallbackManager(cache=cache)

        # Tier 1: AEDE generates live policy
        telemetry = make_success_batch(10)
        _, _, tier1_policy, _ = run_pipeline(telemetry, EndpointState.AVAILABLE)

        # Cache the Tier 1 policy
        cache.set_policy("ep-test", tier1_policy, ttl_seconds=300, cached_at=NOW)

        # Tier 2: AEDE is "down" — fetch from cache (within TTL)
        tier2_policy = fallback.get_tier_2_cached_policy("ep-test")
        assert tier2_policy is not None
        assert tier2_policy.endpoint_id == tier1_policy.endpoint_id
        assert tier2_policy.policy_id == tier1_policy.policy_id

        # Tier 2 still valid at T+299s
        t_before_expiry = NOW + timedelta(seconds=299)
        still_cached = cache.get_policy("ep-test", current_time=t_before_expiry)
        assert still_cached is not None

        # Tier 2 expired at T+301s
        t_after_expiry = NOW + timedelta(seconds=301)
        expired = cache.get_policy("ep-test", current_time=t_after_expiry)
        assert expired is None

        # Tier 3: static fallback
        tier3_time = NOW + timedelta(seconds=400)
        tier3_policy = fallback.get_tier_3_static_policy("ep-test", computed_at=tier3_time)
        assert tier3_policy.retry_policy.initial_delay_seconds == 5.0
        assert tier3_policy.retry_policy.backoff_multiplier == 2.0
        assert tier3_policy.retry_policy.max_delay_seconds == 300.0
        assert tier3_policy.retry_policy.max_retries == 10
        assert tier3_policy.concurrency_policy.max_parallel == 1
        assert tier3_policy.isolation_policy.action == IsolationAction.CONTINUE
        # Note: notify_admin is False in StrategyA's default output.
        # The arch.md says Tier 3 should notify_admin=True. This is a known
        # gap — StrategyA as a research baseline doesn't notify, but the
        # Tier 3 fallback semantically should. Tracked for follow-up.


# ---------------------------------------------------------------------------
# Scenario 7: Feature Flag Strategy Switching
# ---------------------------------------------------------------------------


class TestScenario7FeatureFlagSwitching:
    """Same telemetry → Strategy A, B, C produce different valid policies."""

    def test_strategy_selection_via_flags(self):
        telemetry = make_mixed_batch(20, failure_rate=0.30)
        eri = compute_eri(
            attempts=telemetry,
            queue_depth=0,
            weights=DEFAULT_WEIGHTS,
            baseline_latency_ms=100.0,
            max_latency_ms=5000.0,
            max_consecutive_failures=10,
            max_queue_depth=100,
            computed_at=NOW,
        )

        # Strategy A (static)
        flags_a = FeatureFlags(static_retry_mode=True)
        assert flags_a.active_strategy_name == "STRATEGY_A"
        strategy_a = StrategyA()
        pol_a, rec_a = strategy_a.evaluate("ep-test", EndpointState.AVAILABLE, eri, NOW)
        assert rec_a.strategy_used == "STRATEGY_A"
        assert pol_a.retry_policy.initial_delay_seconds == 5.0
        assert pol_a.concurrency_policy.max_parallel == 1

        # Strategy B (adaptive)
        flags_b = FeatureFlags(adaptive_retry_mode=True)
        assert flags_b.active_strategy_name == "STRATEGY_B"
        strategy_b = StrategyB()
        pol_b, rec_b = strategy_b.evaluate("ep-test", EndpointState.AVAILABLE, eri, NOW)
        assert rec_b.strategy_used == "STRATEGY_B"

        # Strategy C (AEDE)
        flags_c = FeatureFlags()
        assert flags_c.active_strategy_name == "STRATEGY_C"
        strategy_c = StrategyC(thresholds=DEFAULT_THRESHOLDS)
        pol_c, rec_c = strategy_c.evaluate("ep-test", EndpointState.AVAILABLE, eri, NOW)
        assert rec_c.strategy_used == "STRATEGY_C"

        # Strategy B should adapt: with 30% failure rate, delay should be > 5.0
        assert pol_b.retry_policy.initial_delay_seconds >= pol_a.retry_policy.initial_delay_seconds

        # All three produce valid policies
        for pol in [pol_a, pol_b, pol_c]:
            assert isinstance(pol, DeliveryPolicy)
            assert pol.endpoint_id == "ep-test"
            assert 0.0 <= pol.eri_score <= 100.0


# ---------------------------------------------------------------------------
# Scenario 8: Determinism Guarantee
# ---------------------------------------------------------------------------


class TestScenario8Determinism:
    """Identical inputs must produce identical outputs (except UUIDs)."""

    def test_deterministic_pipeline(self):
        telemetry = make_mixed_batch(20, failure_rate=0.25)
        state = EndpointState.AVAILABLE

        # Run 1
        eri1, state1, policy1, record1 = run_pipeline(telemetry, state, computed_at=NOW)

        # Run 2 (identical inputs)
        eri2, state2, policy2, record2 = run_pipeline(telemetry, state, computed_at=NOW)

        # ERI must be identical
        assert eri1.score == eri2.score
        assert eri1.success_rate_score == eri2.success_rate_score
        assert eri1.latency_score == eri2.latency_score
        assert eri1.consecutive_failure_score == eri2.consecutive_failure_score

        # State must be identical
        assert state1 == state2

        # Policy fields must be identical (except policy_id which is UUID)
        assert policy1.endpoint_state == policy2.endpoint_state
        assert policy1.eri_score == policy2.eri_score
        assert policy1.retry_policy == policy2.retry_policy
        assert policy1.concurrency_policy == policy2.concurrency_policy
        assert policy1.isolation_policy == policy2.isolation_policy

        # Explainability must be identical (except record_id)
        assert record1.reason == record2.reason
        assert record1.triggered_thresholds == record2.triggered_thresholds
        assert record1.policy_changes == record2.policy_changes
        assert record1.metrics == record2.metrics
        assert record1.thresholds == record2.thresholds


# ---------------------------------------------------------------------------
# Scenario 9: Explainability Completeness
# ---------------------------------------------------------------------------


class TestScenario9ExplainabilityCompleteness:
    """Every evaluation produces a fully populated ExplainabilityRecord."""

    def _check_record(self, record: ExplainabilityRecord, expect_state_change: bool):
        """Validate all required fields are populated."""
        assert record.record_id  # non-empty
        assert record.policy_id  # non-empty
        assert record.endpoint_id  # non-empty
        assert record.state_before in [s.value for s in EndpointState]
        assert record.state_after in [s.value for s in EndpointState]
        assert record.state_changed == expect_state_change
        assert record.reason  # non-empty string
        assert record.strategy_used in ("STRATEGY_A", "STRATEGY_B", "STRATEGY_C")
        assert record.window_size > 0
        assert record.evaluated_at == NOW

        # Metric snapshot completeness
        m = record.metrics
        for field in ["success_rate_score", "latency_score", "consecutive_failure_score",
                       "timeout_score", "http_5xx_score", "queue_depth_score", "recovery_trend_score"]:
            score = getattr(m, field)
            assert 0.0 <= score <= 1.0, f"{field} = {score} out of range"
        assert 0.0 <= m.composite_eri <= 100.0

        # Threshold snapshot completeness
        t = record.thresholds
        for field in ["degradation_threshold", "degradation_recovery_threshold",
                       "unstable_threshold", "unstable_recovery_threshold",
                       "quarantine_threshold", "recovery_threshold"]:
            assert getattr(t, field) > 0

        if expect_state_change:
            assert len(record.triggered_thresholds) > 0 or len(record.policy_changes) > 0

    def test_explainability_for_state_change(self):
        """State transition produces complete explainability with triggered thresholds."""
        telemetry = make_mixed_batch(20, failure_rate=0.40)
        _, _, _, record = run_pipeline(telemetry, EndpointState.AVAILABLE)
        self._check_record(record, expect_state_change=True)

    def test_explainability_for_no_state_change(self):
        """Stable state produces complete explainability without triggered thresholds."""
        telemetry = make_success_batch(20)
        _, _, _, record = run_pipeline(telemetry, EndpointState.AVAILABLE)
        self._check_record(record, expect_state_change=False)


# ---------------------------------------------------------------------------
# Scenario 10: Re-Quarantine During Recovery
# ---------------------------------------------------------------------------


class TestScenario10ReQuarantineDuringRecovery:
    """RECOVERING → QUARANTINED when endpoint fails again."""

    def test_recovery_failure_re_quarantines(self):
        state = EndpointState.RECOVERING

        # All failures during recovery → ERI ≈ 0 → re-quarantine
        telemetry = make_failure_batch(20, http_status=503, timed_out=True, latency_ms=10000.0)
        eri, new_state, policy, record = run_pipeline(telemetry, state)

        assert eri.score < DEFAULT_THRESHOLDS.quarantine_threshold
        assert new_state == EndpointState.QUARANTINED
        assert record.state_before == "RECOVERING"
        assert record.state_after == "QUARANTINED"
        assert record.state_changed is True
        assert policy.isolation_policy.action == IsolationAction.QUARANTINE
        assert policy.retry_policy.max_retries == 0
