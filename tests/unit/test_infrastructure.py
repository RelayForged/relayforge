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

    def test_ttl_fires_without_explicit_current_time(self, sample_policy):
        """Bug fix: TTL must fire even when caller omits current_time.

        Previously get_policy() only checked expiry when current_time was
        truthy, so callers that omitted it got stale policies forever.
        (Person A audit, Declined 3, Bug 3a)
        """
        import time
        cache = InMemoryPolicyCache(default_ttl_seconds=1)
        # Entry cached with wall-clock time and zero TTL
        cache.set_policy("ep-stale", sample_policy, ttl_seconds=0)
        time.sleep(0.01)

        # Omitting current_time — should still expire via wall-clock check
        assert cache.get_policy("ep-stale") is None

    def test_set_policy_uses_aware_timestamps(self, sample_policy):
        """Bug fix: set_policy must store timezone-aware timestamps.

        Previously datetime.now() (naive) was used, causing TypeError
        when get_policy compared against timezone-aware current_time.
        (Person A audit, Declined 3, Bug 3b)
        """
        cache = InMemoryPolicyCache()
        cache.set_policy("ep-tz", sample_policy)

        # This must not raise TypeError on aware vs naive subtraction.
        aware_now = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
        result = cache.get_policy("ep-tz", current_time=aware_now)
        # Policy was just cached, so it should still be valid.
        assert result is not None

    def test_cache_max_size_eviction(self, sample_policy):
        """Bug fix: cache must not grow unbounded.

        (Person A audit, Declined 3, Bug 3c)
        """
        cache = InMemoryPolicyCache(max_size=3)
        for i in range(5):
            cache.set_policy(f"ep-{i}", sample_policy, cached_at=NOW)

        # Only 3 entries should remain
        assert len(cache._cache) == 3
        # Oldest entries (ep-0, ep-1) should have been evicted
        assert cache.get_policy("ep-0", current_time=NOW) is None
        assert cache.get_policy("ep-1", current_time=NOW) is None
        # Newest entries should survive
        assert cache.get_policy("ep-4", current_time=NOW) is not None

    def test_tier_2_returns_none_when_expired(self, sample_policy):
        """Tier 2 must not serve stale policies after TTL expiry."""
        import time
        cache = InMemoryPolicyCache(default_ttl_seconds=1)
        cache.set_policy("ep-tier2", sample_policy, ttl_seconds=0)
        time.sleep(0.01)

        fallback_mgr = PolicyFallbackManager(cache=cache)
        assert fallback_mgr.get_tier_2_cached_policy("ep-tier2") is None

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
