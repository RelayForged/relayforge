"""
Policy Synthesizer — the Control Plane module that maps EndpointState and ERI
into an immutable DeliveryPolicy and ExplainabilityRecord.

This is the core research contribution of Relay:
"AEDE (Control Plane) computes Delivery Policies based on endpoint state and ERI.
 Workers (Data Plane) execute those policies. Workers never make runtime decisions."

Deterministic: given identical inputs, always returns identical policies.
Time boundaries are passed as explicit parameters (computed_at); datetime.now() is never called internally.
"""

from datetime import datetime
from typing import Tuple

from relay.control_plane.explainability.record import (
    ExplainabilityRecord,
    MetricSnapshot,
    ThresholdSnapshot,
)
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
from relay.kernel.value_objects.endpoint_state import (
    EndpointState,
    StateMachineThresholds,
)
from relay.kernel.value_objects.eri import ERIScore


def synthesize_policy(
    endpoint_id: str,
    state_before: EndpointState,
    state_after: EndpointState,
    eri: ERIScore,
    thresholds: StateMachineThresholds,
    computed_at: datetime,
) -> Tuple[DeliveryPolicy, ExplainabilityRecord]:
    """
    Synthesizes an immutable DeliveryPolicy and matching ExplainabilityRecord.

    Args:
        endpoint_id: Target endpoint ID.
        state_before: Previous EndpointState.
        state_after: Current EndpointState (determined by state machine).
        eri: Computed ERIScore object.
        thresholds: Current StateMachineThresholds configuration.
        computed_at: Explicit timestamp for determinism.

    Returns:
        tuple of (DeliveryPolicy, ExplainabilityRecord).
    """
    state_changed = state_before != state_after

    # 1. Derive sub-policies based on state_after
    if state_after == EndpointState.AVAILABLE:
        retry_pol = RetryPolicy(
            initial_delay_seconds=2.0,
            max_delay_seconds=60.0,
            max_retries=10,
            backoff_strategy=BackoffStrategy.EXPONENTIAL,
            backoff_multiplier=2.0,
        )
        concurrency_pol = ConcurrencyPolicy(max_parallel=10, queue_priority=10)
        isolation_pol = IsolationPolicy(action=IsolationAction.CONTINUE)
        replay_pol = ReplayPolicy(allowed=False, batch_size=50, speed=1.0)
        notification_pol = NotificationPolicy(
            notify_admin=False, alert_level=AlertLevel.INFO, suppress=False
        )

    elif state_after == EndpointState.OBSERVED_DEGRADATION:
        retry_pol = RetryPolicy(
            initial_delay_seconds=5.0,
            max_delay_seconds=120.0,
            max_retries=8,
            backoff_strategy=BackoffStrategy.EXPONENTIAL,
            backoff_multiplier=2.0,
        )
        concurrency_pol = ConcurrencyPolicy(max_parallel=3, queue_priority=5)
        isolation_pol = IsolationPolicy(action=IsolationAction.REDUCE_CONCURRENCY)
        replay_pol = ReplayPolicy(allowed=False, batch_size=20, speed=0.8)
        notification_pol = NotificationPolicy(
            notify_admin=False, alert_level=AlertLevel.WARNING, suppress=False
        )

    elif state_after == EndpointState.UNSTABLE:
        retry_pol = RetryPolicy(
            initial_delay_seconds=15.0,
            max_delay_seconds=300.0,
            max_retries=5,
            backoff_strategy=BackoffStrategy.EXPONENTIAL,
            backoff_multiplier=2.0,
        )
        concurrency_pol = ConcurrencyPolicy(max_parallel=1, queue_priority=2)
        isolation_pol = IsolationPolicy(action=IsolationAction.PAUSE)
        replay_pol = ReplayPolicy(allowed=False, batch_size=10, speed=0.5)
        notification_pol = NotificationPolicy(
            notify_admin=True, alert_level=AlertLevel.WARNING, suppress=False
        )

    elif state_after == EndpointState.QUARANTINED:
        retry_pol = RetryPolicy(
            initial_delay_seconds=300.0,
            max_delay_seconds=300.0,
            max_retries=0,
            backoff_strategy=BackoffStrategy.FIXED,
        )
        concurrency_pol = ConcurrencyPolicy(max_parallel=1, queue_priority=0)
        isolation_pol = IsolationPolicy(action=IsolationAction.QUARANTINE)
        replay_pol = ReplayPolicy(allowed=False, batch_size=10, speed=1.0)
        notification_pol = NotificationPolicy(
            notify_admin=True, alert_level=AlertLevel.CRITICAL, suppress=False
        )

    elif state_after == EndpointState.RECOVERING:
        retry_pol = RetryPolicy(
            initial_delay_seconds=10.0,
            max_delay_seconds=180.0,
            max_retries=6,
            backoff_strategy=BackoffStrategy.EXPONENTIAL,
            backoff_multiplier=1.5,
        )
        concurrency_pol = ConcurrencyPolicy(max_parallel=1, queue_priority=8)
        isolation_pol = IsolationPolicy(action=IsolationAction.CONTINUE)
        replay_pol = ReplayPolicy(allowed=True, batch_size=10, speed=0.5)
        notification_pol = NotificationPolicy(
            notify_admin=False, alert_level=AlertLevel.INFO, suppress=False
        )
    else:
        raise ValueError(f"Unknown EndpointState: {state_after}")

    integrity_pol = IntegrityPolicy(hmac_required=False)

    # 2. Construct immutable DeliveryPolicy
    policy = DeliveryPolicy(
        endpoint_id=endpoint_id,
        endpoint_state=state_after.value,
        eri_score=eri.score,
        computed_at=computed_at,
        retry_policy=retry_pol,
        concurrency_policy=concurrency_pol,
        isolation_policy=isolation_pol,
        replay_policy=replay_pol,
        notification_policy=notification_pol,
        integrity_policy=integrity_pol,
    )

    # 3. Construct Metric and Threshold snapshots
    metric_snapshot = MetricSnapshot(
        success_rate_score=eri.success_rate_score,
        latency_score=eri.latency_score,
        consecutive_failure_score=eri.consecutive_failure_score,
        timeout_score=eri.timeout_score,
        http_5xx_score=eri.http_5xx_score,
        queue_depth_score=eri.queue_depth_score,
        recovery_trend_score=eri.recovery_trend_score,
        composite_eri=eri.score,
    )

    threshold_snapshot = ThresholdSnapshot(
        degradation_threshold=thresholds.degradation_threshold,
        degradation_recovery_threshold=thresholds.degradation_recovery_threshold,
        unstable_threshold=thresholds.unstable_threshold,
        unstable_recovery_threshold=thresholds.unstable_recovery_threshold,
        quarantine_threshold=thresholds.quarantine_threshold,
        recovery_threshold=thresholds.recovery_threshold,
    )

    # 4. Generate human-readable explainability reasoning
    triggered_thresholds = []
    policy_changes = []

    if eri.score <= thresholds.quarantine_threshold:
        triggered_thresholds.append(
            f"ERI {eri.score:.1f} <= quarantine_threshold {thresholds.quarantine_threshold:.1f}"
        )
    elif eri.score <= thresholds.unstable_threshold:
        triggered_thresholds.append(
            f"ERI {eri.score:.1f} <= unstable_threshold {thresholds.unstable_threshold:.1f}"
        )
    elif eri.score <= thresholds.degradation_threshold:
        triggered_thresholds.append(
            f"ERI {eri.score:.1f} <= degradation_threshold {thresholds.degradation_threshold:.1f}"
        )

    if state_changed:
        reason = (
            f"Endpoint state changed from {state_before.value} to {state_after.value} "
            f"(ERI = {eri.score:.1f}). Policy updated to match {state_after.value} operational profile."
        )
        policy_changes.append(f"state: {state_before.value} → {state_after.value}")
        policy_changes.append(f"max_parallel → {concurrency_pol.max_parallel}")
        policy_changes.append(f"isolation_action → {isolation_pol.action.value}")
    else:
        reason = (
            f"Endpoint state remains {state_after.value} (ERI = {eri.score:.1f}). "
            f"Existing policy profile maintained."
        )

    record = ExplainabilityRecord(
        policy_id=policy.policy_id,
        endpoint_id=endpoint_id,
        state_before=state_before.value,
        state_after=state_after.value,
        state_changed=state_changed,
        metrics=metric_snapshot,
        thresholds=threshold_snapshot,
        reason=reason,
        triggered_thresholds=triggered_thresholds,
        policy_changes=policy_changes,
        strategy_used="STRATEGY_C",
        evaluated_at=computed_at,
        window_size=eri.window_size,
    )

    return policy, record
