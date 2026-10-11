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


class BenchmarkResultsResponse(BaseModel):
    """Aggregated benchmark run results."""
    runs: List[BenchmarkRunResponse] = Field(default_factory=list)


class EndpointSummary(BaseModel):
    """Overview item for an endpoint in the endpoint listing."""
    endpoint_id: str
    state: str
    eri_score: Optional[float] = None
    last_evaluated_at: Optional[str] = None


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
