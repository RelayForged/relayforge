"""
Unit tests for Policy Synthesizer and Strategy A, B, C implementations.
"""

import pytest
from datetime import datetime, timezone

from relay.control_plane.aede.synthesizer import synthesize_policy
from relay.control_plane.aede.strategies.static import StrategyA
from relay.control_plane.aede.strategies.adaptive import StrategyB
from relay.control_plane.aede.strategies.aede_strategy import StrategyC
from relay.kernel.policies.delivery_policy import IsolationAction, BackoffStrategy
from relay.kernel.value_objects.endpoint_state import EndpointState, StateMachineThresholds
from relay.kernel.value_objects.eri import ERIScore


NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def thresholds():
    return StateMachineThresholds(
        degradation_threshold=80.0,
        degradation_recovery_threshold=90.0,
        unstable_threshold=60.0,
        unstable_recovery_threshold=70.0,
        quarantine_threshold=30.0,
        recovery_threshold=90.0,
    )


class TestPolicySynthesizer:
    def test_synthesize_available_policy(self, thresholds):
        eri = ERIScore.perfect(window_size=10, computed_at=NOW)
        policy, record = synthesize_policy(
            endpoint_id="ep-101",
            state_before=EndpointState.AVAILABLE,
            state_after=EndpointState.AVAILABLE,
            eri=eri,
            thresholds=thresholds,
            computed_at=NOW,
        )

        assert policy.endpoint_state == "AVAILABLE"
        assert policy.concurrency_policy.max_parallel == 10
        assert policy.isolation_policy.action == IsolationAction.CONTINUE
        assert policy.notification_policy.notify_admin is False
        assert record.state_changed is False

    def test_synthesize_degraded_policy(self, thresholds):
        eri = ERIScore.perfect(window_size=10, computed_at=NOW)
        object.__setattr__(eri, "score", 75.0)

        policy, record = synthesize_policy(
            endpoint_id="ep-101",
            state_before=EndpointState.AVAILABLE,
            state_after=EndpointState.OBSERVED_DEGRADATION,
            eri=eri,
            thresholds=thresholds,
            computed_at=NOW,
        )

        assert policy.endpoint_state == "OBSERVED_DEGRADATION"
        assert policy.concurrency_policy.max_parallel == 3
        assert policy.isolation_policy.action == IsolationAction.REDUCE_CONCURRENCY
        assert record.state_changed is True
        assert "AVAILABLE → OBSERVED_DEGRADATION" in record.policy_changes[0]

    def test_synthesize_quarantined_policy(self, thresholds):
        eri = ERIScore.zero(window_size=10, computed_at=NOW)

        policy, record = synthesize_policy(
            endpoint_id="ep-101",
            state_before=EndpointState.UNSTABLE,
            state_after=EndpointState.QUARANTINED,
            eri=eri,
            thresholds=thresholds,
            computed_at=NOW,
        )

        assert policy.endpoint_state == "QUARANTINED"
        assert policy.isolation_policy.action == IsolationAction.QUARANTINE
        assert policy.notification_policy.notify_admin is True
        assert policy.retry_policy.max_retries == 0


class TestStrategies:
    def test_strategy_a(self, thresholds):
        strategy = StrategyA()
        eri = ERIScore.zero(window_size=10, computed_at=NOW)
        policy, record = strategy.evaluate("ep-101", EndpointState.AVAILABLE, eri, NOW)

        assert record.strategy_used == "STRATEGY_A"
        assert policy.retry_policy.initial_delay_seconds == 5.0
        assert policy.concurrency_policy.max_parallel == 1
        assert policy.isolation_policy.action == IsolationAction.CONTINUE

    def test_strategy_b_scaling(self, thresholds):
        strategy = StrategyB()

        # Healthy history -> low initial delay
        eri_healthy = ERIScore.perfect(window_size=10, computed_at=NOW)
        p_healthy, r_healthy = strategy.evaluate("ep-101", EndpointState.AVAILABLE, eri_healthy, NOW)
        assert p_healthy.retry_policy.initial_delay_seconds == 5.0
        assert p_healthy.concurrency_policy.max_parallel == 5

        # Degraded success rate -> higher delay, lower max_parallel
        eri_bad = ERIScore.zero(window_size=10, computed_at=NOW)
        p_bad, r_bad = strategy.evaluate("ep-101", EndpointState.AVAILABLE, eri_bad, NOW)
        assert p_bad.retry_policy.initial_delay_seconds == 50.0  # 5.0 / 0.1
        assert p_bad.concurrency_policy.max_parallel == 1

    def test_strategy_c_full_engine(self, thresholds):
        strategy = StrategyC(thresholds=thresholds)

        # High ERI -> remains AVAILABLE
        eri_perfect = ERIScore.perfect(window_size=10, computed_at=NOW)
        p1, r1 = strategy.evaluate("ep-101", EndpointState.AVAILABLE, eri_perfect, NOW)
        assert p1.endpoint_state == "AVAILABLE"
        assert r1.strategy_used == "STRATEGY_C"

        # Low ERI -> transitions to QUARANTINED
        eri_zero = ERIScore.zero(window_size=10, computed_at=NOW)
        p2, r2 = strategy.evaluate("ep-101", EndpointState.AVAILABLE, eri_zero, NOW)
        assert p2.endpoint_state == "QUARANTINED"
        assert r2.state_changed is True
