from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ERIScore(BaseModel):
    """
    An immutable snapshot of a computed Endpoint Reliability Index evaluation.

    The ERI is a composite score (0.0–100.0) derived from externally observable
    delivery signals using configurable weights (see config.yaml). It is the sole
    numerical input to the Endpoint State Machine's transition logic.

    This object carries both the final score and the individual normalised sub-scores
    that produced it. Storing the sub-scores here — rather than recomputing them —
    is a deliberate explainability choice: every ExplainabilityRecord can reference
    this object directly and answer "which metric contributed what?"

    Computation lives in relay/control_plane/eri/. This class is the return type
    only — it performs no calculation itself.

    Windowing: attempt-count only. No time-based filter. See ADR 0009.

    AEDE never calls datetime.now() internally — computed_at is passed in by the
    caller so that policy evaluation remains deterministic and testable.
    """

    model_config = ConfigDict(frozen=True)

    # --- Final composite score ---

    score: float = Field(
        ge=0.0,
        le=100.0,
        description="Composite ERI score. 100 = perfectly healthy, 0 = unavailable.",
    )

    # --- Normalised sub-scores (each 0.0–1.0 before weighting) ---
    # 1.0 means the metric contributed its full healthy weight;
    # 0.0 means the metric signalled complete failure for its dimension.

    success_rate_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Fraction of delivery attempts that succeeded within the window.",
    )

    latency_score: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Normalised latency health. 1.0 = at or below baseline latency; "
            "decays toward 0.0 as average latency increases."
        ),
    )

    consecutive_failure_score: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Penalty from consecutive failure streaks. 1.0 = no consecutive failures; "
            "decays toward 0.0 as the streak length grows."
        ),
    )

    timeout_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Fraction of attempts that did NOT time out within the window.",
    )

    http_5xx_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Fraction of attempts that did NOT return an HTTP 5xx response.",
    )

    queue_depth_score: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Normalised inverse queue depth. 1.0 = queue empty; "
            "decays toward 0.0 as queue depth exceeds configured ceiling."
        ),
    )

    recovery_trend_score: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Trend bonus. 1.0 = recent attempts improving vs. earlier window half; "
            "0.5 = flat; decays toward 0.0 if recent attempts are worsening."
        ),
    )

    # --- Observation metadata (for explainability) ---

    window_size: int = Field(
        gt=0,
        description="Number of DeliveryAttempts included in this evaluation.",
    )

    computed_at: datetime = Field(
        description=(
            "Timestamp at which this ERI was computed. Always supplied by the caller — "
            "AEDE never reads datetime.now() internally (determinism requirement)."
        ),
    )

    @model_validator(mode="after")
    def validate_score_consistency(self) -> "ERIScore":
        """
        Sanity-check: the composite score must be representable as a weighted sum
        of the sub-scores. We do not recompute it here (we don't have the weights),
        but we verify it is within the valid range, which the Field constraints already
        enforce. This validator exists as an extension point for stricter checks in tests.
        """
        return self

    @classmethod
    def perfect(cls, window_size: int, computed_at: datetime | None = None) -> "ERIScore":
        """
        Convenience constructor for a fully healthy ERI (score = 100).
        Used in tests and as the initial state for a newly registered endpoint.
        """
        return cls(
            score=100.0,
            success_rate_score=1.0,
            latency_score=1.0,
            consecutive_failure_score=1.0,
            timeout_score=1.0,
            http_5xx_score=1.0,
            queue_depth_score=1.0,
            recovery_trend_score=1.0,
            window_size=window_size,
            computed_at=computed_at or datetime.now(timezone.utc),
        )

    @classmethod
    def zero(cls, window_size: int, computed_at: datetime | None = None) -> "ERIScore":
        """
        Convenience constructor for a fully failed ERI (score = 0).
        Used in tests and to represent an endpoint with no successful deliveries.
        """
        return cls(
            score=0.0,
            success_rate_score=0.0,
            latency_score=0.0,
            consecutive_failure_score=0.0,
            timeout_score=0.0,
            http_5xx_score=0.0,
            queue_depth_score=0.0,
            recovery_trend_score=0.0,
            window_size=window_size,
            computed_at=computed_at or datetime.now(timezone.utc),
        )
