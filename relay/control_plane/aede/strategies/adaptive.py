"""
Strategy B — Adaptive Baseline Delivery Strategy.

Adjusts retry intervals based on recent success rate, but has NO state machine,
NO hysteresis, and NO quarantine isolation.

Used in research benchmarking to isolate the value of the Endpoint State Machine
versus simple adaptive backoff.
(See docs/execution-plan.md § Strategy B).
"""

from datetime import datetime
from typing import Tuple

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
    IntegrityPolicy,
    IsolationAction,
    IsolationPolicy,
    NotificationPolicy,
    ReplayPolicy,
    RetryPolicy,
)
from relay.kernel.value_objects.endpoint_state import EndpointState
from relay.kernel.value_objects.eri import ERIScore


class StrategyB(DeliveryStrategy):
    """
    Adaptive baseline strategy (Strategy B).
    Scales retry interval based on success_rate_score without state machine.
    """

    @property
    def strategy_name(self) -> str:
        return "STRATEGY_B"

    def evaluate(
        self,
        endpoint_id: str,
        endpoint_state: EndpointState,
        eri: ERIScore,
        computed_at: datetime,
    ) -> Tuple[DeliveryPolicy, ExplainabilityRecord]:
        # Scale delay inversely with success rate score (floor at 0.1 to prevent div by zero)
        effective_success = max(eri.success_rate_score, 0.1)
        scaled_delay = min(5.0 / effective_success, 300.0)

        # Scale concurrency based on success rate
        max_parallel = 5 if eri.success_rate_score > 0.8 else (2 if eri.success_rate_score > 0.4 else 1)

        policy = DeliveryPolicy(
            endpoint_id=endpoint_id,
            endpoint_state=endpoint_state.value,
            eri_score=eri.score,
            computed_at=computed_at,
            retry_policy=RetryPolicy(
                initial_delay_seconds=scaled_delay,
                max_delay_seconds=300.0,
                max_retries=10,
                backoff_strategy=BackoffStrategy.EXPONENTIAL,
                backoff_multiplier=2.0,
            ),
            concurrency_policy=ConcurrencyPolicy(
                max_parallel=max_parallel,
                queue_priority=5,
            ),
            isolation_policy=IsolationPolicy(
                action=IsolationAction.CONTINUE,
            ),
            replay_policy=ReplayPolicy(allowed=False),
            notification_policy=NotificationPolicy(
                notify_admin=eri.success_rate_score < 0.3,
                alert_level=AlertLevel.WARNING if eri.success_rate_score < 0.3 else AlertLevel.INFO,
            ),
            integrity_policy=IntegrityPolicy(hmac_required=False),
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
                degradation_threshold=80.0,
                degradation_recovery_threshold=90.0,
                unstable_threshold=60.0,
                unstable_recovery_threshold=70.0,
                quarantine_threshold=30.0,
                recovery_threshold=90.0,
            ),
            reason=f"Strategy B active: initial_delay scaled to {scaled_delay:.1f}s based on success rate {eri.success_rate_score:.2f}.",
            triggered_thresholds=[],
            policy_changes=[f"scaled_delay: {scaled_delay:.1f}s", f"max_parallel: {max_parallel}"],
            strategy_used=self.strategy_name,
            evaluated_at=computed_at,
            window_size=eri.window_size,
        )

        return policy, record
