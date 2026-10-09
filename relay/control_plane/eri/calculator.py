from datetime import datetime
from typing import Optional, Sequence
from relay.kernel.interfaces.telemetry_protocol import TelemetryProtocol
from relay.kernel.value_objects.eri import ERIScore
from relay.kernel.value_objects.eri import ERIWeights


def compute_eri(
    attempts: Sequence[TelemetryProtocol],
    queue_depth: int,
    weights: ERIWeights,
    baseline_latency_ms: float,
    max_latency_ms: float,
    max_consecutive_failures: int,
    max_queue_depth: int,
    computed_at: datetime,
    window_duration_seconds: Optional[float] = None,
) -> ERIScore:
    if window_duration_seconds is not None:
        attempts = [
            a for a in attempts
            if (computed_at - a.observed_at).total_seconds() <= window_duration_seconds
        ]

    if not attempts:
        return ERIScore.perfect(window_size=0, computed_at=computed_at)

    total_attempts = len(attempts)

    # 1. Success rate score
    successes = sum(1 for a in attempts if a.succeeded)
    success_rate_score = successes / total_attempts

    # 2. Timeout score
    timeouts = sum(1 for a in attempts if a.timed_out)
    timeout_score = (total_attempts - timeouts) / total_attempts

    # 3. HTTP 5xx score
    http_5xx_count = sum(1 for a in attempts if a.is_http_5xx)
    http_5xx_score = (total_attempts - http_5xx_count) / total_attempts

    # 4. Latency score (linear decay from baseline to max_latency)
    valid_latencies = [a.latency_ms for a in attempts if not a.timed_out]
    if not valid_latencies:
        latency_score = 0.0
    else:
        avg_latency = sum(valid_latencies) / len(valid_latencies)
        if avg_latency <= baseline_latency_ms:
            latency_score = 1.0
        elif avg_latency >= max_latency_ms:
            latency_score = 0.0
        else:
            latency_score = 1.0 - ((avg_latency - baseline_latency_ms) / (max_latency_ms - baseline_latency_ms))

    # 5. Consecutive failure score (counting backward from the tail)
    streak = 0
    for attempt in reversed(attempts):
        if not attempt.succeeded:
            streak += 1
        else:
            break
    
    consecutive_failure_score = max(0.0, 1.0 - (streak / max_consecutive_failures))

    # 6. Queue depth score
    queue_depth_score = max(0.0, 1.0 - (queue_depth / max_queue_depth))

    # 7. Recovery trend score (compare second half to first half)
    if total_attempts < 2:
        recovery_trend_score = 1.0
    else:
        midpoint = total_attempts // 2
        first_half = attempts[:midpoint]
        second_half = attempts[midpoint:]
        
        first_half_sr = sum(1 for a in first_half if a.succeeded) / len(first_half)
        second_half_sr = sum(1 for a in second_half if a.succeeded) / len(second_half)
        
        if second_half_sr > first_half_sr or (second_half_sr == 1.0 and first_half_sr == 1.0):
            recovery_trend_score = 1.0
        elif second_half_sr == first_half_sr:
            recovery_trend_score = 0.5
        else:
            recovery_trend_score = 0.0

    # Composite Score
    composite_score = (
        success_rate_score * weights.success_rate_weight
        + latency_score * weights.latency_weight
        + consecutive_failure_score * weights.consecutive_failures_weight
        + timeout_score * weights.timeout_weight
        + http_5xx_score * weights.http_5xx_weight
        + queue_depth_score * weights.queue_depth_weight
        + recovery_trend_score * weights.recovery_trend_weight
    ) * 100.0

    return ERIScore(
        score=composite_score,
        success_rate_score=success_rate_score,
        latency_score=latency_score,
        consecutive_failure_score=consecutive_failure_score,
        timeout_score=timeout_score,
        http_5xx_score=http_5xx_score,
        queue_depth_score=queue_depth_score,
        recovery_trend_score=recovery_trend_score,
        window_size=total_attempts,
        computed_at=computed_at,
    )
