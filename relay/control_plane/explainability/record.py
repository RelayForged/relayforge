"""
ExplainabilityRecord — an immutable record of why a policy decision was made.

Every policy evaluation produces an ExplainabilityRecord. This record answers
the question: "Why did AEDE make this decision?" It captures the metrics that
triggered the decision, which thresholds were crossed, what state transition
occurred, and what policy changes were applied.

This is a core requirement of the architecture:
    "Principle 2 — Explainability Before Automation: Every automated decision
     must be explainable." (docs/architecture.md § Architectural Principles)

ExplainabilityRecords are immutable. They are never updated or deleted.
The Dashboard reads and displays them. They constitute a primary piece of
the research evaluation.
"""

from datetime import datetime
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class MetricSnapshot(BaseModel):
    """
    A frozen snapshot of the individual ERI sub-scores at evaluation time.

    Stored inside the ExplainabilityRecord so that every decision can be
    traced back to the exact metric values that produced it — without
    needing to recompute ERI from historical telemetry.
    """

    model_config = ConfigDict(frozen=True)

    success_rate_score: float = Field(ge=0.0, le=1.0)
    latency_score: float = Field(ge=0.0, le=1.0)
    consecutive_failure_score: float = Field(ge=0.0, le=1.0)
    timeout_score: float = Field(ge=0.0, le=1.0)
    http_5xx_score: float = Field(ge=0.0, le=1.0)
    queue_depth_score: float = Field(ge=0.0, le=1.0)
    recovery_trend_score: float = Field(ge=0.0, le=1.0)
    composite_eri: float = Field(ge=0.0, le=100.0)


class ThresholdSnapshot(BaseModel):
    """
    The state machine thresholds in effect at the time of evaluation.

    Stored so that retrospective analysis can determine whether the
    same decision would be made under different threshold configurations.
    """

    model_config = ConfigDict(frozen=True)

    degradation_threshold: float
    degradation_recovery_threshold: float
    unstable_threshold: float
    unstable_recovery_threshold: float
    quarantine_threshold: float
    recovery_threshold: float


class ExplainabilityRecord(BaseModel):
    """
    An immutable, human-readable record of why AEDE made a specific
    policy decision.

    Every PolicyEvaluation produces exactly one ExplainabilityRecord.
    The Dashboard's explainability panel renders these records directly.
    """

    model_config = ConfigDict(frozen=True)

    # --- Identity ---

    record_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Globally unique identifier for this record.",
    )
    policy_id: str = Field(
        description="The DeliveryPolicy this record explains.",
    )
    endpoint_id: str = Field(
        description="The endpoint that was evaluated.",
    )

    # --- Decision context ---

    state_before: str = Field(
        description="EndpointState before the decision.",
    )
    state_after: str = Field(
        description="EndpointState after the decision.",
    )
    state_changed: bool = Field(
        description="Whether a state transition occurred.",
    )

    # --- Metrics that drove the decision ---

    metrics: MetricSnapshot = Field(
        description="Individual ERI sub-scores at decision time.",
    )
    thresholds: ThresholdSnapshot = Field(
        description="State machine thresholds in effect at decision time.",
    )

    # --- Human-readable reasoning ---

    reason: str = Field(
        description=(
            "A human-readable explanation of why this decision was made. "
            "Example: 'Success rate fell to 40%, ERI = 72, transitioning to "
            "OBSERVED_DEGRADATION — monitoring frequency increased, "
            "parallel delivery reduced.'"
        ),
    )
    triggered_thresholds: list[str] = Field(
        default_factory=list,
        description=(
            "List of threshold descriptions that were crossed. "
            "Example: ['ERI 72.0 < degradation_threshold 80.0']"
        ),
    )
    policy_changes: list[str] = Field(
        default_factory=list,
        description=(
            "List of policy field changes applied. "
            "Example: ['max_parallel: 5 → 2', 'backoff_strategy: FIXED → EXPONENTIAL']"
        ),
    )

    # --- Strategy context ---

    strategy_used: str = Field(
        description="Which strategy produced this decision: STRATEGY_A, STRATEGY_B, or STRATEGY_C.",
    )

    # --- Timing ---

    evaluated_at: datetime = Field(
        description="When this evaluation occurred. Caller-supplied, never datetime.now().",
    )
    window_size: int = Field(
        ge=0,
        description="Number of telemetry records in the observation window.",
    )
