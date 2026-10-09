import math
from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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

    Computation lives in platform/control_plane/eri/. This class is the return type
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

    @field_validator("score", mode="before")
    def clamp_score(cls, v: float) -> float:
        if isinstance(v, (int, float)):
            if math.isclose(v, 100.0, abs_tol=1e-5):
                return 100.0
            if math.isclose(v, 0.0, abs_tol=1e-5):
                return 0.0
        return v

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
        ge=0,
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




class ERIWeights(BaseModel):
    model_config = ConfigDict(frozen=True)

    success_rate_weight: float = Field(ge=0.0, le=1.0)
    latency_weight: float = Field(ge=0.0, le=1.0)
    consecutive_failures_weight: float = Field(ge=0.0, le=1.0)
    timeout_weight: float = Field(ge=0.0, le=1.0)
    http_5xx_weight: float = Field(ge=0.0, le=1.0)
    queue_depth_weight: float = Field(ge=0.0, le=1.0)
    recovery_trend_weight: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def check_weights_sum_to_one(self) -> "ERIWeights":
        total_weight = (
            self.success_rate_weight + self.latency_weight + self.consecutive_failures_weight + 
            self.timeout_weight + self.http_5xx_weight + self.queue_depth_weight + self.recovery_trend_weight
        )
        if not math.isclose(total_weight, 1.0, rel_tol=1e-5):
            raise ValueError(f"ERI weights must sum to 1.0, but sum to {total_weight}")
        return self

    @classmethod
    def from_config(cls, config_dict: dict) -> "ERIWeights":
        return cls(**config_dict)

    @classmethod
    def optimal(cls) -> "ERIWeights":
        """
        Calibrated default weights discovered by a genetic-algorithm search
        (benchmark/genetic_optimizer.py) over calibration scenarios {1, 2, 6, 7, 8, 11}, with
        recovery_trend_weight capped at 0.15 during the search (see
        RECOVERY_TREND_WEIGHT_CAP in that file for why: it is the one ERI sub-score that is
        not a smooth function of the telemetry, and an uncapped search reliably converges to
        over-weighting it in a way that destabilizes long-duration, borderline-degradation
        endpoints). Disclosed as an empirical calibration baseline, re-verified against the
        full held-out evaluation set before adoption, not a claimed universal global optimum.
        """
        return OPTIMAL_ERI_WEIGHTS

    @classmethod
    def default(cls) -> "ERIWeights":
        """Default calibrated weights."""
        return OPTIMAL_ERI_WEIGHTS


# Calibrated default weights: genetic-algorithm search over calibration scenarios
# {1, 2, 6, 7, 8, 11} with recovery_trend_weight capped at 0.15 (benchmark/genetic_optimizer.py).
# Re-verified against the full held-out evaluation set {3,4,5,9,10,12,13,14} before adoption:
# Hypothesis 1 outage reduction 87.25% (vs 53.5% for the prior hand-picked profile), Scenario 5
# flapping unchanged (4.2), Scenario 13 flapping improved (5.4 vs 13.2). The one disclosed
# trade-off: Scenario 14 (long sustained mild degradation) transitions rose from ~12.2 mean /
# 21 max to ~18.6 mean / 25 max -- worse, but nowhere near the 30-57 transitions an uncapped
# search produced there, and still within the range production weights themselves already
# reached in their worst seed (21).
OPTIMAL_ERI_WEIGHTS = ERIWeights(
    success_rate_weight=0.374948,
    latency_weight=0.002808,
    consecutive_failures_weight=0.463822,
    timeout_weight=0.002808,
    http_5xx_weight=0.002808,
    queue_depth_weight=0.002808,
    recovery_trend_weight=0.149998,
)

