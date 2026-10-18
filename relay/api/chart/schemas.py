"""Pydantic response models for the Chart Data API.

These are presentation DTOs tailored for the React dashboard frontend.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class EndpointStateResponse(BaseModel):
    """Current state of an endpoint."""
    endpoint_id: str
    state: str
    last_evaluated_at: Optional[str] = None


class ERIHistoryPoint(BaseModel):
    """Single point in an ERI time-series chart."""
    timestamp: str
    score: float
    state: str
    sub_scores: Dict[str, float] = Field(default_factory=dict)


class ERIHistoryResponse(BaseModel):
    """ERI history response for Recharts LineChart."""
    endpoint_id: str
    points: List[ERIHistoryPoint] = Field(default_factory=list)


class PolicyDecisionResponse(BaseModel):
    """Latest policy decision and active operational configuration."""
    endpoint_id: str
    policy_id: str
    strategy_used: str
    is_fallback: bool
    fallback_tier: int
    eri_score: float
    state_before: str
    state_after: str
    state_changed: bool
    retry_policy: Dict[str, Any] = Field(default_factory=dict)
    concurrency_policy: Dict[str, Any] = Field(default_factory=dict)
    isolation_action: str


class ExplainabilityResponse(BaseModel):
    """An individual explainability audit record."""
    record_id: str
    endpoint_id: str
    policy_id: str
    state_before: str
    state_after: str
    state_changed: bool
    reason: str
    triggered_thresholds: List[str] = Field(default_factory=list)
    policy_changes: List[str] = Field(default_factory=list)
    strategy_used: str
    evaluated_at: str
    metrics: Dict[str, Any] = Field(default_factory=dict)
    thresholds: Dict[str, Any] = Field(default_factory=dict)


class ExplainabilityListResponse(BaseModel):
    """List of explainability records for the explainability panel."""
    endpoint_id: str
    records: List[ExplainabilityResponse] = Field(default_factory=list)


class BenchmarkRunResponse(BaseModel):
    """Benchmark run execution statistics."""
    run_id: str
    scenario: str
    strategy: str
    total_events: int
    successful_deliveries: int
    failed_deliveries: int
    avg_retry_count: float
    avg_recovery_time_seconds: float
    started_at: str
    finished_at: str
    scenario_id: Optional[int] = None
    seed: Optional[int] = None
    delivery_success_rate: Optional[float] = None
    total_wasted_retries: Optional[int] = None
    recovery_time_events: Optional[int] = None
    quarantine_count: Optional[int] = None
    total_delay_seconds: Optional[float] = None


class BenchmarkResultsResponse(BaseModel):
    """Aggregated benchmark run results."""
    runs: List[BenchmarkRunResponse] = Field(default_factory=list)


class EndpointSummary(BaseModel):
    """Overview item for an endpoint in the endpoint listing."""
    endpoint_id: str
    state: str
    eri_score: Optional[float] = None
    last_evaluated_at: Optional[str] = None
    # From the endpoint registry, so the dashboard can name the endpoint.
    target_url: Optional[str] = None
    owner_id: Optional[str] = None


class EndpointListResponse(BaseModel):
    """List of registered endpoints and their live statuses."""
    endpoints: List[EndpointSummary] = Field(default_factory=list)


class DeadLetterItem(BaseModel):
    """A delivery that will not complete on its own."""
    delivery_id: str
    event_id: str
    event_type: str
    endpoint_id: str
    reason: str  # FAILED, DEAD_LETTER, or PARKED
    attempt_count: int
    last_error: Optional[str] = None
    failed_at: str


class DeadLetterListResponse(BaseModel):
    """Dead letters for the DLQ panel, oldest first."""
    items: List[DeadLetterItem] = Field(default_factory=list)


class BenchmarkRunRequest(BaseModel):
    """Which simulated experiments to run. Defaults to the held-out evaluation set."""
    scenario_ids: Optional[List[int]] = None
    strategies: Optional[List[str]] = None
    seed: int = 42


class MetricComparison(BaseModel):
    """One metric for Strategy C against a baseline, summed over the scenarios run."""
    strategy_c: float
    baseline: float
    # Relative change of C against the baseline, in percent. None when the
    # baseline is zero and a percentage would be meaningless.
    pct_change: Optional[float] = None


class StrategyComparison(BaseModel):
    baseline: str
    scenarios: List[int]
    wasted_retries: MetricComparison
    total_delay_seconds: MetricComparison
    # Mean delivery success rate; pct_change is in percentage points here.
    success_rate: MetricComparison


class BenchmarkRunResultResponse(BaseModel):
    """Runs produced by one POST /benchmark/run, plus C-versus-baseline summaries."""
    runs: List[BenchmarkRunResponse] = Field(default_factory=list)
    comparison: Dict[str, StrategyComparison] = Field(default_factory=dict)


class ContentionEndpointResult(BaseModel):
    endpoint_id: str
    total_events: int
    successful_deliveries: int
    throughput_events_per_sec: float
    avg_queue_wait_ms: float
    p95_queue_wait_ms: float
    total_retries: int


class MultiEndpointStrategyResult(BaseModel):
    strategy: str
    quarantine_triggered: bool
    total_sim_time_sec: float
    failing: ContentionEndpointResult
    healthy: ContentionEndpointResult


class MultiEndpointResponse(BaseModel):
    """Noisy-neighbor simulation: one failing and one healthy endpoint sharing a worker pool."""
    worker_pool_size: int
    strategies: List[MultiEndpointStrategyResult] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Scenario Lab: step-by-step playback of one benchmark scenario
# --------------------------------------------------------------------------


class ScenarioPhaseInfo(BaseModel):
    """One phase of a scenario's fault script, in the simulator's own terms."""
    description: str
    events: int
    success_rate: float
    latency_ms: float
    timed_out_rate: float
    http_5xx_rate: float
    manual_restore: bool


class ScenarioInfo(BaseModel):
    scenario_id: int
    name: str
    description: str
    # "held_out": used only for evaluation. "tuning": used to calibrate the
    # ERI weights, so results on it are not reported as evidence.
    evaluation_set: str
    total_events: int
    phases: List[ScenarioPhaseInfo] = Field(default_factory=list)


class ScenarioListResponse(BaseModel):
    scenarios: List[ScenarioInfo] = Field(default_factory=list)


class TraceStep(BaseModel):
    """One simulated event, as the strategy handled it."""
    phase: int
    succeeded: bool
    eri: float
    state: str
    retries: int
    delay_seconds: float
    max_parallel: int


class StrategyTrace(BaseModel):
    strategy: str
    steps: List[TraceStep] = Field(default_factory=list)
    delivery_success_rate: float
    total_wasted_retries: int
    total_delay_seconds: float
    quarantine_count: int


class ScenarioTraceResponse(BaseModel):
    """Every event of one scenario under each strategy, for animated playback."""
    scenario: ScenarioInfo
    seed: int
    strategies: List[StrategyTrace] = Field(default_factory=list)


class ContentionTrace(BaseModel):
    """
    One strategy's noisy-neighbour run, job by job, for playback.

    Each job is [endpoint, event_index, arrival_ms, start_ms, end_ms, worker,
    succeeded, retries]; endpoint 0 is the failing one and 1 the healthy
    one, and worker is -1 for a job that never reached a worker.
    """
    strategy: str
    quarantined_from_event: Optional[int] = None
    total_sim_time_sec: float
    jobs: List[List[float]] = Field(default_factory=list)


class MultiEndpointTraceResponse(BaseModel):
    worker_pool_size: int
    events_per_endpoint: int
    arrival_interval_ms: float
    failing_from_event: int
    strategies: List[ContentionTrace] = Field(default_factory=list)
