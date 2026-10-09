import pytest
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

from relay.kernel.value_objects.eri import ERIWeights
from relay.control_plane.eri.calculator import compute_eri


@dataclass
class MockTelemetry:
    attempt_id: str
    endpoint_id: str
    succeeded: bool
    http_status: Optional[int]
    latency_ms: float
    timed_out: bool
    observed_at: datetime
    
    @property
    def is_http_5xx(self) -> bool:
        return self.http_status is not None and 500 <= self.http_status <= 599


@pytest.fixture
def standard_weights():
    return ERIWeights(
        success_rate_weight=0.35,
        latency_weight=0.20,
        consecutive_failures_weight=0.20,
        timeout_weight=0.10,
        http_5xx_weight=0.05,
        queue_depth_weight=0.05,
        recovery_trend_weight=0.05,
    )


def test_compute_eri_empty_list(standard_weights):
    computed_at = datetime.now(timezone.utc)
    eri = compute_eri(
        attempts=[],
        queue_depth=0,
        weights=standard_weights,
        baseline_latency_ms=100.0,
        max_latency_ms=2000.0,
        max_consecutive_failures=10,
        max_queue_depth=1000,
        computed_at=computed_at,
    )
    
    assert eri.score == 100.0
    assert eri.window_size == 0


def test_compute_eri_perfect_history(standard_weights):
    computed_at = datetime.now(timezone.utc)
    attempts = [
        MockTelemetry(
            attempt_id=f"att_{i}",
            endpoint_id="ep_1",
            succeeded=True,
            http_status=200,
            latency_ms=50.0,
            timed_out=False,
            observed_at=computed_at
        ) for i in range(10)
    ]
    
    eri = compute_eri(
        attempts=attempts,
        queue_depth=0,
        weights=standard_weights,
        baseline_latency_ms=100.0,
        max_latency_ms=2000.0,
        max_consecutive_failures=10,
        max_queue_depth=1000,
        computed_at=computed_at,
    )
    
    assert eri.score == 100.0
    assert eri.success_rate_score == 1.0


def test_compute_eri_mixed_history(standard_weights):
    computed_at = datetime.now(timezone.utc)
    attempts = []
    
    # 8 successes
    for i in range(8):
        attempts.append(MockTelemetry(
            attempt_id=f"att_{i}",
            endpoint_id="ep_1",
            succeeded=True,
            http_status=200,
            latency_ms=150.0,  # Slight latency penalty
            timed_out=False,
            observed_at=computed_at
        ))
        
    # 2 failures at the end (current streak = 2)
    for i in range(8, 10):
        attempts.append(MockTelemetry(
            attempt_id=f"att_{i}",
            endpoint_id="ep_1",
            succeeded=False,
            http_status=503,
            latency_ms=1500.0,
            timed_out=False,
            observed_at=computed_at
        ))
        
    eri = compute_eri(
        attempts=attempts,
        queue_depth=100, # 100/1000 = 0.1 queue depth penalty
        weights=standard_weights,
        baseline_latency_ms=100.0,
        max_latency_ms=2000.0,
        max_consecutive_failures=10,
        max_queue_depth=1000,
        computed_at=computed_at,
    )
    
    assert eri.success_rate_score == 0.8
    assert eri.consecutive_failure_score == 0.8  # 1.0 - (2/10)
    assert eri.http_5xx_score == 0.8  # 2 out of 10 were 5xx
    assert eri.queue_depth_score == 0.9  # 1.0 - (100/1000)
    
    # Latency: (8 * 150 + 2 * 1500) / 10 = (1200 + 3000) / 10 = 420 ms
    # Penalty: (420 - 100) / (2000 - 100) = 320 / 1900 = 0.1684
    # Score: 1.0 - 0.1684 = 0.8316
    assert 0.83 < eri.latency_score < 0.84
    
    # Overall score should be appropriately weighted
    assert 0.0 < eri.score < 100.0


def test_eri_weights_optimal_and_default():
    """Verify ERIWeights.optimal() and default() return valid, normalized weights."""
    opt = ERIWeights.optimal()
    dflt = ERIWeights.default()

    assert opt == dflt
    assert opt.success_rate_weight == 0.374948
    assert opt.latency_weight == 0.002808
    assert opt.consecutive_failures_weight == 0.463822
    assert opt.timeout_weight == 0.002808
    assert opt.http_5xx_weight == 0.002808
    assert opt.queue_depth_weight == 0.002808
    assert opt.recovery_trend_weight == 0.149998

    total = (
        opt.success_rate_weight
        + opt.latency_weight
        + opt.consecutive_failures_weight
        + opt.timeout_weight
        + opt.http_5xx_weight
        + opt.queue_depth_weight
        + opt.recovery_trend_weight
    )
    assert abs(total - 1.0) < 1e-6


def test_eri_score_clamp_bounds():
    """Verify ERIScore before validator clamps floating-point inaccuracies near bounds."""
    from relay.kernel.value_objects.eri import ERIScore

    now = datetime.now(timezone.utc)
    # 100.00000000000003 should be clamped to 100.0 without validation error
    eri_top = ERIScore(
        score=100.00000000000003,
        success_rate_score=1.0,
        latency_score=1.0,
        consecutive_failure_score=1.0,
        timeout_score=1.0,
        http_5xx_score=1.0,
        queue_depth_score=1.0,
        recovery_trend_score=1.0,
        window_size=10,
        computed_at=now,
    )
    assert eri_top.score == 100.0

    # -0.00000000000001 should be clamped to 0.0 without validation error
    eri_bottom = ERIScore(
        score=-0.00000000000001,
        success_rate_score=0.0,
        latency_score=0.0,
        consecutive_failure_score=0.0,
        timeout_score=0.0,
        http_5xx_score=0.0,
        queue_depth_score=0.0,
        recovery_trend_score=0.0,
        window_size=10,
        computed_at=now,
    )
    assert eri_bottom.score == 0.0


def test_compute_eri_window_duration_filters_stale(standard_weights):
    """Verify window_duration_seconds filters out stale historical attempts."""
    from datetime import timedelta

    computed_at = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)
    # 5 stale failures from 10 minutes ago
    stale_failures = [
        MockTelemetry(
            attempt_id=f"stale_{i}",
            endpoint_id="ep_1",
            succeeded=False,
            http_status=500,
            latency_ms=2000.0,
            timed_out=False,
            observed_at=computed_at - timedelta(seconds=600),
        )
        for i in range(5)
    ]
    # 5 fresh successes from 10 seconds ago
    fresh_successes = [
        MockTelemetry(
            attempt_id=f"fresh_{i}",
            endpoint_id="ep_1",
            succeeded=True,
            http_status=200,
            latency_ms=50.0,
            timed_out=False,
            observed_at=computed_at - timedelta(seconds=10),
        )
        for i in range(5)
    ]
    all_attempts = stale_failures + fresh_successes

    # Without window filtering: degraded by stale failures
    eri_all = compute_eri(
        attempts=all_attempts,
        queue_depth=0,
        weights=standard_weights,
        baseline_latency_ms=100.0,
        max_latency_ms=2000.0,
        max_consecutive_failures=10,
        max_queue_depth=1000,
        computed_at=computed_at,
    )
    assert eri_all.window_size == 10
    assert eri_all.score < 80.0

    # With window_duration_seconds=60: only fresh successes remain
    eri_filtered = compute_eri(
        attempts=all_attempts,
        queue_depth=0,
        weights=standard_weights,
        baseline_latency_ms=100.0,
        max_latency_ms=2000.0,
        max_consecutive_failures=10,
        max_queue_depth=1000,
        computed_at=computed_at,
        window_duration_seconds=60.0,
    )
    assert eri_filtered.window_size == 5
    assert eri_filtered.score == 100.0
    assert eri_filtered.success_rate_score == 1.0


def test_compute_eri_window_duration_all_expired(standard_weights):
    """Verify compute_eri returns perfect score if all attempts are outside the time window."""
    from datetime import timedelta

    computed_at = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)
    stale_failures = [
        MockTelemetry(
            attempt_id=f"stale_{i}",
            endpoint_id="ep_1",
            succeeded=False,
            http_status=503,
            latency_ms=3000.0,
            timed_out=True,
            observed_at=computed_at - timedelta(seconds=300),
        )
        for i in range(5)
    ]

    eri = compute_eri(
        attempts=stale_failures,
        queue_depth=0,
        weights=standard_weights,
        baseline_latency_ms=100.0,
        max_latency_ms=2000.0,
        max_consecutive_failures=10,
        max_queue_depth=1000,
        computed_at=computed_at,
        window_duration_seconds=30.0,
    )
    assert eri.window_size == 0
    assert eri.score == 100.0

