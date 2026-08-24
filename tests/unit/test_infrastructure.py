"""
Unit tests for FeatureFlags, PolicyCache, Three-Tier Fallback Manager,
and ExplainabilityRepository.
"""

import pytest
from datetime import datetime, timezone

from relay.infrastructure.feature_flags import FeatureFlags
from relay.infrastructure.cache import InMemoryPolicyCache, PolicyFallbackManager
from relay.infrastructure.persistence.explainability_repo import InMemoryExplainabilityRepository
from relay.kernel.policies.delivery_policy import (
    DeliveryPolicy, RetryPolicy, ConcurrencyPolicy, IsolationPolicy,
    BackoffStrategy, IsolationAction
)
from relay.control_plane.explainability.record import (
    ExplainabilityRecord, MetricSnapshot, ThresholdSnapshot
)


NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def sample_policy():
    return DeliveryPolicy(
        endpoint_id="ep-test",
        endpoint_state="AVAILABLE",
        eri_score=100.0,
        computed_at=NOW,
        retry_policy=RetryPolicy(
            initial_delay_seconds=2.0,
            max_delay_seconds=60.0,
            max_retries=5,
            backoff_strategy=BackoffStrategy.EXPONENTIAL,
        ),
        concurrency_policy=ConcurrencyPolicy(max_parallel=5),
        isolation_policy=IsolationPolicy(action=IsolationAction.CONTINUE),
    )


class TestFeatureFlags:
    def test_default_flags(self):
        flags = FeatureFlags()
        assert flags.aede_enabled is True
        assert flags.eri_enabled is True
        assert flags.state_machine_enabled is True
        assert flags.active_strategy_name == "STRATEGY_C"

    def test_static_retry_mode_override(self):
        flags = FeatureFlags(static_retry_mode=True)
        assert flags.active_strategy_name == "STRATEGY_A"

    def test_adaptive_retry_mode_override(self):
        flags = FeatureFlags(adaptive_retry_mode=True)
        assert flags.active_strategy_name == "STRATEGY_B"

    def test_disabled_aede_falls_to_strategy_a(self):
        flags = FeatureFlags(aede_enabled=False)
        assert flags.active_strategy_name == "STRATEGY_A"


class TestPolicyCacheAndFallback:
    def test_cache_get_set(self, sample_policy):
        cache = InMemoryPolicyCache(default_ttl_seconds=300)
        cache.set_policy("ep-test", sample_policy, cached_at=NOW)

        # Retrieve before expiry
        retrieved = cache.get_policy("ep-test", current_time=NOW)
        assert retrieved is not None
        assert retrieved.endpoint_id == "ep-test"

    def test_cache_ttl_expiry(self, sample_policy):
        cache = InMemoryPolicyCache(default_ttl_seconds=300)
        cache.set_policy("ep-test", sample_policy, ttl_seconds=300, cached_at=NOW)

        # Retrieve after 301 seconds -> expired
        future = datetime(2026, 8, 4, 12, 5, 1, tzinfo=timezone.utc)
        assert cache.get_policy("ep-test", current_time=future) is None

    def test_tier_3_static_policy(self):
        cache = InMemoryPolicyCache()
        fallback_mgr = PolicyFallbackManager(cache=cache)

        tier_3_policy = fallback_mgr.get_tier_3_static_policy("ep-fallback", computed_at=NOW)
        assert tier_3_policy.endpoint_id == "ep-fallback"
        assert tier_3_policy.retry_policy.initial_delay_seconds == 5.0
        assert tier_3_policy.concurrency_policy.max_parallel == 1


class TestExplainabilityRepository:
    def test_save_and_retrieve(self):
        repo = InMemoryExplainabilityRepository()
        record = ExplainabilityRecord(
            policy_id="pol-001",
            endpoint_id="ep-001",
            state_before="AVAILABLE",
            state_after="AVAILABLE",
            state_changed=False,
            metrics=MetricSnapshot(
                success_rate_score=1.0, latency_score=1.0,
                consecutive_failure_score=1.0, timeout_score=1.0,
                http_5xx_score=1.0, queue_depth_score=1.0,
                recovery_trend_score=1.0, composite_eri=100.0
            ),
            thresholds=ThresholdSnapshot(
                degradation_threshold=80.0, degradation_recovery_threshold=90.0,
                unstable_threshold=60.0, unstable_recovery_threshold=70.0,
                quarantine_threshold=30.0, recovery_threshold=90.0
            ),
            reason="All metrics perfect.",
            strategy_used="STRATEGY_C",
            evaluated_at=NOW,
            window_size=10,
        )

        repo.save(record)
        by_policy = repo.get_by_policy_id("pol-001")
        assert by_policy is not None
        assert by_policy.reason == "All metrics perfect."

        latest_by_ep = repo.get_latest_by_endpoint("ep-001")
        assert latest_by_ep is not None
        assert latest_by_ep.policy_id == "pol-001"
