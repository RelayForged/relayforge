"""
Tests for DeliveryPolicy entity, sub-policies, PolicyEvaluation,
ExplainabilityRecord, and DeliveryStrategy interface.
"""

import pytest
from datetime import datetime, timezone

from relay.kernel.policies.delivery_policy import (
    DeliveryPolicy,
    RetryPolicy,
    ConcurrencyPolicy,
    IsolationPolicy,
    ReplayPolicy,
    NotificationPolicy,
    IntegrityPolicy,
    BackoffStrategy,
    IsolationAction,
    AlertLevel,
)
from relay.kernel.entities.policy_evaluation import PolicyEvaluation
from relay.control_plane.explainability.record import (
    ExplainabilityRecord,
    MetricSnapshot,
    ThresholdSnapshot,
)
from relay.kernel.contracts.delivery_strategy import DeliveryStrategy
from relay.kernel.value_objects.endpoint_state import EndpointState
from relay.kernel.value_objects.eri import ERIScore


NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_retry_policy():
    return RetryPolicy(
        initial_delay_seconds=5.0,
        max_delay_seconds=300.0,
        max_retries=10,
        backoff_strategy=BackoffStrategy.EXPONENTIAL,
        backoff_multiplier=2.0,
    )


@pytest.fixture
def sample_delivery_policy(sample_retry_policy):
    return DeliveryPolicy(
        endpoint_id="ep-123",
        endpoint_state="AVAILABLE",
        eri_score=95.0,
        computed_at=NOW,
        retry_policy=sample_retry_policy,
        concurrency_policy=ConcurrencyPolicy(max_parallel=5),
        isolation_policy=IsolationPolicy(action=IsolationAction.CONTINUE),
    )


@pytest.fixture
def sample_metrics():
    return MetricSnapshot(
        success_rate_score=0.95,
        latency_score=0.85,
        consecutive_failure_score=1.0,
        timeout_score=0.9,
        http_5xx_score=0.98,
        queue_depth_score=1.0,
        recovery_trend_score=1.0,
        composite_eri=92.0,
    )


@pytest.fixture
def sample_thresholds():
    return ThresholdSnapshot(
        degradation_threshold=80.0,
        degradation_recovery_threshold=90.0,
        unstable_threshold=60.0,
        unstable_recovery_threshold=70.0,
        quarantine_threshold=30.0,
        recovery_threshold=90.0,
    )


# ---------------------------------------------------------------------------
# DeliveryPolicy tests
# ---------------------------------------------------------------------------


class TestDeliveryPolicy:
    def test_create_minimal_policy(self, sample_retry_policy):
        """Policy can be created with only required sub-policies."""
        policy = DeliveryPolicy(
            endpoint_id="ep-001",
            endpoint_state="AVAILABLE",
            eri_score=100.0,
            computed_at=NOW,
            retry_policy=sample_retry_policy,
            concurrency_policy=ConcurrencyPolicy(max_parallel=5),
            isolation_policy=IsolationPolicy(action=IsolationAction.CONTINUE),
        )
        assert policy.endpoint_id == "ep-001"
        assert policy.version == 1
        assert policy.replay_policy.allowed is False
        assert policy.notification_policy.notify_admin is False
        assert policy.integrity_policy.hmac_required is False

    def test_policy_is_immutable(self, sample_delivery_policy):
        """DeliveryPolicy and all sub-policies are frozen."""
        with pytest.raises(Exception):
            sample_delivery_policy.eri_score = 50.0

    def test_policy_has_unique_id(self, sample_retry_policy):
        """Each policy gets a unique ID by default."""
        p1 = DeliveryPolicy(
            endpoint_id="ep-001",
            endpoint_state="AVAILABLE",
            eri_score=100.0,
            computed_at=NOW,
            retry_policy=sample_retry_policy,
            concurrency_policy=ConcurrencyPolicy(max_parallel=1),
            isolation_policy=IsolationPolicy(action=IsolationAction.CONTINUE),
        )
        p2 = DeliveryPolicy(
            endpoint_id="ep-001",
            endpoint_state="AVAILABLE",
            eri_score=100.0,
            computed_at=NOW,
            retry_policy=sample_retry_policy,
            concurrency_policy=ConcurrencyPolicy(max_parallel=1),
            isolation_policy=IsolationPolicy(action=IsolationAction.CONTINUE),
        )
        assert p1.policy_id != p2.policy_id

    def test_eri_score_bounds(self, sample_retry_policy):
        """ERI score must be 0-100."""
        with pytest.raises(ValueError):
            DeliveryPolicy(
                endpoint_id="ep-001",
                endpoint_state="AVAILABLE",
                eri_score=101.0,
                computed_at=NOW,
                retry_policy=sample_retry_policy,
                concurrency_policy=ConcurrencyPolicy(max_parallel=1),
                isolation_policy=IsolationPolicy(action=IsolationAction.CONTINUE),
            )


class TestRetryPolicy:
    def test_valid_retry_policy(self):
        rp = RetryPolicy(
            initial_delay_seconds=5.0,
            max_delay_seconds=300.0,
            max_retries=10,
            backoff_strategy=BackoffStrategy.EXPONENTIAL,
        )
        assert rp.backoff_multiplier == 2.0  # default
        assert rp.deadline_seconds is None

    def test_invalid_initial_delay(self):
        with pytest.raises(ValueError):
            RetryPolicy(
                initial_delay_seconds=0,
                max_delay_seconds=300.0,
                max_retries=10,
                backoff_strategy=BackoffStrategy.FIXED,
            )

    def test_zero_retries_allowed(self):
        """max_retries=0 means no retries (fire-and-forget)."""
        rp = RetryPolicy(
            initial_delay_seconds=1.0,
            max_delay_seconds=1.0,
            max_retries=0,
            backoff_strategy=BackoffStrategy.FIXED,
        )
        assert rp.max_retries == 0


class TestIsolationPolicy:
    def test_all_actions(self):
        for action in IsolationAction:
            ip = IsolationPolicy(action=action)
            assert ip.action == action


# ---------------------------------------------------------------------------
# PolicyEvaluation tests
# ---------------------------------------------------------------------------


class TestPolicyEvaluation:
    def test_create_evaluation(self):
        ev = PolicyEvaluation(
            endpoint_id="ep-123",
            policy_id="pol-456",
            explainability_record_id="expl-789",
            eri_score=72.0,
            state_before="AVAILABLE",
            state_after="OBSERVED_DEGRADATION",
            state_changed=True,
            strategy_used="STRATEGY_C",
            evaluated_at=NOW,
            window_size=100,
        )
        assert ev.state_changed is True
        assert ev.fallback_tier == 1
        assert ev.is_fallback is False

    def test_fallback_evaluation(self):
        ev = PolicyEvaluation(
            endpoint_id="ep-123",
            policy_id="pol-456",
            explainability_record_id="expl-789",
            eri_score=100.0,
            state_before="AVAILABLE",
            state_after="AVAILABLE",
            state_changed=False,
            strategy_used="STRATEGY_A",
            is_fallback=True,
            fallback_tier=3,
            evaluated_at=NOW,
            window_size=0,
        )
        assert ev.is_fallback is True
        assert ev.fallback_tier == 3

    def test_evaluation_is_immutable(self):
        ev = PolicyEvaluation(
            endpoint_id="ep-123",
            policy_id="pol-456",
            explainability_record_id="expl-789",
            eri_score=50.0,
            state_before="UNSTABLE",
            state_after="QUARANTINED",
            state_changed=True,
            strategy_used="STRATEGY_C",
            evaluated_at=NOW,
            window_size=100,
        )
        with pytest.raises(Exception):
            ev.eri_score = 80.0


# ---------------------------------------------------------------------------
# ExplainabilityRecord tests
# ---------------------------------------------------------------------------


class TestExplainabilityRecord:
    def test_create_record(self, sample_metrics, sample_thresholds):
        record = ExplainabilityRecord(
            policy_id="pol-456",
            endpoint_id="ep-123",
            state_before="AVAILABLE",
            state_after="OBSERVED_DEGRADATION",
            state_changed=True,
            metrics=sample_metrics,
            thresholds=sample_thresholds,
            reason="Success rate fell to 40%, ERI = 72, transitioning to OBSERVED_DEGRADATION.",
            triggered_thresholds=["ERI 72.0 < degradation_threshold 80.0"],
            policy_changes=["max_parallel: 5 → 2", "backoff_strategy: FIXED → EXPONENTIAL"],
            strategy_used="STRATEGY_C",
            evaluated_at=NOW,
            window_size=100,
        )
        assert record.state_changed is True
        assert len(record.triggered_thresholds) == 1
        assert len(record.policy_changes) == 2
        assert record.metrics.composite_eri == 92.0

    def test_record_is_immutable(self, sample_metrics, sample_thresholds):
        record = ExplainabilityRecord(
            policy_id="pol-456",
            endpoint_id="ep-123",
            state_before="AVAILABLE",
            state_after="AVAILABLE",
            state_changed=False,
            metrics=sample_metrics,
            thresholds=sample_thresholds,
            reason="No change.",
            strategy_used="STRATEGY_C",
            evaluated_at=NOW,
            window_size=50,
        )
        with pytest.raises(Exception):
            record.reason = "Modified"


# ---------------------------------------------------------------------------
# DeliveryStrategy interface tests
# ---------------------------------------------------------------------------


class TestDeliveryStrategyInterface:
    def test_cannot_instantiate_abstract(self):
        """DeliveryStrategy is abstract and cannot be instantiated directly."""
        with pytest.raises(TypeError):
            DeliveryStrategy()

    def test_concrete_implementation(self):
        """A concrete subclass can be instantiated if it implements all methods."""

        class DummyStrategy(DeliveryStrategy):
            @property
            def strategy_name(self) -> str:
                return "DUMMY"

            def evaluate(self, endpoint_id, endpoint_state, eri, computed_at):
                policy = DeliveryPolicy(
                    endpoint_id=endpoint_id,
                    endpoint_state=endpoint_state.value,
                    eri_score=eri.score,
                    computed_at=computed_at,
                    retry_policy=RetryPolicy(
                        initial_delay_seconds=1.0,
                        max_delay_seconds=60.0,
                        max_retries=3,
                        backoff_strategy=BackoffStrategy.FIXED,
                    ),
                    concurrency_policy=ConcurrencyPolicy(max_parallel=1),
                    isolation_policy=IsolationPolicy(action=IsolationAction.CONTINUE),
                )
                # Minimal explainability record
                record = ExplainabilityRecord(
                    policy_id=policy.policy_id,
                    endpoint_id=endpoint_id,
                    state_before=endpoint_state.value,
                    state_after=endpoint_state.value,
                    state_changed=False,
                    metrics=MetricSnapshot(
                        success_rate_score=1.0,
                        latency_score=1.0,
                        consecutive_failure_score=1.0,
                        timeout_score=1.0,
                        http_5xx_score=1.0,
                        queue_depth_score=1.0,
                        recovery_trend_score=1.0,
                        composite_eri=eri.score,
                    ),
                    thresholds=ThresholdSnapshot(
                        degradation_threshold=80.0,
                        degradation_recovery_threshold=90.0,
                        unstable_threshold=60.0,
                        unstable_recovery_threshold=70.0,
                        quarantine_threshold=30.0,
                        recovery_threshold=90.0,
                    ),
                    reason="Dummy strategy — no decision logic.",
                    strategy_used="DUMMY",
                    evaluated_at=computed_at,
                    window_size=0,
                )
                return policy, record

        strategy = DummyStrategy()
        assert strategy.strategy_name == "DUMMY"

        eri = ERIScore.perfect(window_size=10, computed_at=NOW)
        policy, record = strategy.evaluate(
            "ep-001", EndpointState.AVAILABLE, eri, NOW
        )
        assert policy.endpoint_id == "ep-001"
        assert record.strategy_used == "DUMMY"
