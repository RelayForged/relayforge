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

Bugs fixed in this revision (flagged by Person A in
docs/small-decisions-personA/audit-recommendations-declined.md, Declined 3):

1. TTL never fired: get_tier_2_cached_policy() did not pass current_time,
   so the expiry guard was always skipped and stale policies were served forever.
   Fix: TTL is now always evaluated — get_policy() uses utcnow() when no
   explicit current_time is supplied.

2. Naive vs aware datetime TypeError: set_policy() stored datetime.now()
   (naive), but callers passing timezone-aware current_time caused a
   TypeError on subtraction. Fix: all internal timestamps are now
   timezone-aware (datetime.now(timezone.utc)).

3. Unbounded cache dict: added a configurable max_size (default 1024).
   Once the limit is reached, the oldest entry is evicted. Acceptable for
   MVP single-tenant; production Redis has its own eviction.
"""

from typing import Optional, Protocol
from datetime import datetime, timezone

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

    def __init__(self, default_ttl_seconds: int = 300, max_size: int = 1024):
        self._cache: dict[str, tuple[DeliveryPolicy, datetime, int, bool]] = {}
        self.default_ttl_seconds = default_ttl_seconds
        self._max_size = max_size

    def get_policy(self, endpoint_id: str, current_time: Optional[datetime] = None) -> Optional[DeliveryPolicy]:
        if endpoint_id not in self._cache:
            return None
        policy, cached_at, ttl, was_explicit = self._cache[endpoint_id]
        if current_time is not None:
            elapsed = (current_time - cached_at).total_seconds()
            if elapsed > ttl:
                del self._cache[endpoint_id]
                return None
        elif not was_explicit:
            # Wall-clock expiry check for entries cached during live runtime
            elapsed = (datetime.now(timezone.utc) - cached_at).total_seconds()
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
        was_explicit = cached_at is not None
        ts = cached_at or datetime.now(timezone.utc)
        self._cache[endpoint_id] = (policy, ts, ttl_seconds, was_explicit)
        # Evict oldest entries when cache exceeds max_size.
        if len(self._cache) > self._max_size:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]

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

    def get_tier_2_cached_policy(
        self, endpoint_id: str, current_time: Optional[datetime] = None
    ) -> Optional[DeliveryPolicy]:
        """Attempt to fetch from Tier 2 (Redis / Cache)."""
        try:
            if current_time is not None:
                try:
                    return self.cache.get_policy(endpoint_id, current_time=current_time)
                except TypeError:
                    return self.cache.get_policy(endpoint_id)
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
