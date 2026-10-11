"""
Strategy A — Static Baseline Delivery Strategy.

Fixed exponential backoff regardless of endpoint state or ERI score.
Does not monitor endpoint health, does not quarantine, does not adjust concurrency.

Also serves as the Tier 3 fallback policy when AEDE is unreachable.
(See docs/architecture.md § Tier 3 — Static Fallback Policy).
"""

from datetime import datetime
from typing import Tuple

from relay.control_plane.aede.integrity import integrity_policy
from relay.control_plane.explainability.record import (
    ExplainabilityRecord,
    MetricSnapshot,
    ThresholdSnapshot,
)
from relay.kernel.contracts.delivery_strategy import DeliveryStrategy
from relay.kernel.policies.delivery_policy import (
    AlertLevel,
    BackoffStrategy,
    ConcurrencyPolicy,
    DeliveryPolicy,
    IsolationAction,
    IsolationPolicy,
    NotificationPolicy,
    ReplayPolicy,
    RetryPolicy,
)
from relay.kernel.value_objects.endpoint_state import EndpointState
from relay.kernel.value_objects.eri import ERIScore


class StrategyA(DeliveryStrategy):
    """
    Static baseline strategy (Strategy A).
    Implements fixed exponential backoff with max_parallel=1.
    """

    def __init__(self, hmac_required: bool = False) -> None:
        self.hmac_required = hmac_required

    @property
    def strategy_name(self) -> str:
        return "STRATEGY_A"

    def evaluate(
        self,
        endpoint_id: str,
        endpoint_state: EndpointState,
        eri: ERIScore,
        computed_at: datetime,
    ) -> Tuple[DeliveryPolicy, ExplainabilityRecord]:
        policy = DeliveryPolicy(
            endpoint_id=endpoint_id,
            endpoint_state=endpoint_state.value,
            eri_score=eri.score,
            computed_at=computed_at,
            retry_policy=RetryPolicy(
                initial_delay_seconds=5.0,
                max_delay_seconds=300.0,
                max_retries=10,
                backoff_strategy=BackoffStrategy.EXPONENTIAL,
                backoff_multiplier=2.0,
            ),
            concurrency_policy=ConcurrencyPolicy(
                max_parallel=1,
                queue_priority=0,
            ),
            isolation_policy=IsolationPolicy(
                action=IsolationAction.CONTINUE,
            ),
            replay_policy=ReplayPolicy(allowed=False),
            notification_policy=NotificationPolicy(
                notify_admin=False,
                alert_level=AlertLevel.INFO,
            ),
            integrity_policy=integrity_policy(self.hmac_required),
        )

        record = ExplainabilityRecord(
            policy_id=policy.policy_id,
            endpoint_id=endpoint_id,
            state_before=endpoint_state.value,
            state_after=endpoint_state.value,
            state_changed=False,
            metrics=MetricSnapshot(
                success_rate_score=eri.success_rate_score,
                latency_score=eri.latency_score,
                consecutive_failure_score=eri.consecutive_failure_score,
                timeout_score=eri.timeout_score,
                http_5xx_score=eri.http_5xx_score,
                queue_depth_score=eri.queue_depth_score,
                recovery_trend_score=eri.recovery_trend_score,
                composite_eri=eri.score,
            ),
            thresholds=ThresholdSnapshot(
                degradation_threshold=75.0,
                degradation_recovery_threshold=95.0,
                unstable_threshold=55.0,
                unstable_recovery_threshold=85.0,
                quarantine_threshold=30.0,
                recovery_threshold=80.0,
            ),
            reason="Strategy A active: static fixed exponential backoff applied.",
            triggered_thresholds=[],
            policy_changes=[],
            strategy_used=self.strategy_name,
            evaluated_at=computed_at,
            window_size=eri.window_size,
        )

        return policy, record
