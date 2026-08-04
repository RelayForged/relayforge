"""
Policy Cache and Three-Tier Policy Fallback Hierarchy.

Implements Tier 2 (Redis policy cache with 5-minute TTL) and Tier 3 (Static fallback)
when AEDE (Tier 1) is unreachable or disabled.

Hierarchy:
    Tier 1: AEDE live policy (normal operation)
             ↓ if AEDE unreachable/disabled
    Tier 2: Redis-cached policy (last known good policy, TTL: 300s)
             ↓ if cache cold or expired
    Tier 3: Static fallback policy (Strategy A defaults)

See docs/architecture.md § Control Plane Failures (AEDE unavailable).
"""

from typing import Optional, Protocol
import json
from datetime import datetime

from relay.kernel.policies.delivery_policy import DeliveryPolicy
from relay.control_plane.aede.strategies.static import StrategyA
from relay.kernel.value_objects.endpoint_state import EndpointState
from relay.kernel.value_objects.eri import ERIScore


class PolicyCache(Protocol):
    """Protocol contract for policy caching layer."""

    def get_policy(self, endpoint_id: str) -> Optional[DeliveryPolicy]:
        """Fetch cached policy if unexpired."""
        ...

    def set_policy(self, endpoint_id: str, policy: DeliveryPolicy, ttl_seconds: int = 300) -> None:
        """Cache policy with TTL."""
        ...


class InMemoryPolicyCache:
    """
    In-memory implementation of PolicyCache.
    Used for unit testing and local development without Redis.
    """

    def __init__(self, default_ttl_seconds: int = 300):
        self._cache: dict[str, tuple[DeliveryPolicy, datetime, int]] = {}
        self.default_ttl_seconds = default_ttl_seconds

    def get_policy(self, endpoint_id: str, current_time: Optional[datetime] = None) -> Optional[DeliveryPolicy]:
        if endpoint_id not in self._cache:
            return None
        policy, cached_at, ttl = self._cache[endpoint_id]
        if current_time:
            elapsed = (current_time - cached_at).total_seconds()
            if elapsed > ttl:
                del self._cache[endpoint_id]
                return None
        return policy

    def set_policy(
        self,
        endpoint_id: str,
        policy: DeliveryPolicy,
        ttl_seconds: int = 300,
        cached_at: Optional[datetime] = None,
    ) -> None:
        ts = cached_at or datetime.now()
        self._cache[endpoint_id] = (policy, ts, ttl_seconds)

    def clear(self) -> None:
        self._cache.clear()


class PolicyFallbackManager:
    """
    Orchestrates the Three-Tier Policy Fallback hierarchy.
    Ensures workers always get a valid policy even during AEDE outages.
    """

    def __init__(self, cache: PolicyCache):
        self.cache = cache
        self.static_strategy = StrategyA()

    def get_tier_2_cached_policy(self, endpoint_id: str) -> Optional[DeliveryPolicy]:
        """Attempt to fetch from Tier 2 (Redis / Cache)."""
        try:
            return self.cache.get_policy(endpoint_id)
        except Exception:
            return None

    def get_tier_3_static_policy(
        self, endpoint_id: str, computed_at: datetime
    ) -> DeliveryPolicy:
        """Produce Tier 3 (Static Fallback) policy."""
        eri_zero = ERIScore.zero(window_size=0, computed_at=computed_at)
        policy, _ = self.static_strategy.evaluate(
            endpoint_id=endpoint_id,
            endpoint_state=EndpointState.AVAILABLE,
            eri=eri_zero,
            computed_at=computed_at,
        )
        return policy
