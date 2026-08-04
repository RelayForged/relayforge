"""
PolicyEvaluation — an immutable record of a single AEDE execution.

Every time AEDE (or a strategy fallback) evaluates an endpoint, a
PolicyEvaluation is created. It captures the inputs (ERI, state), the
output (DeliveryPolicy), and the reasoning (ExplainabilityRecord ID).

PolicyEvaluations are immutable. Corrections are new records, never
mutations of existing ones. This is a core architectural invariant.
"""

from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class PolicyEvaluation(BaseModel):
    """
    One execution of the policy engine. Links the input observations
    to the output policy and the explainability record that justifies it.

    Stored in PostgreSQL for auditability and timeline reconstruction.
    """

    model_config = ConfigDict(frozen=True)

    evaluation_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Globally unique identifier for this evaluation.",
    )
    endpoint_id: str = Field(
        description="The endpoint that was evaluated.",
    )
    policy_id: str = Field(
        description="The ID of the DeliveryPolicy produced by this evaluation.",
    )
    explainability_record_id: str = Field(
        description="The ID of the ExplainabilityRecord justifying this decision.",
    )

    # --- Input snapshot ---

    eri_score: float = Field(
        ge=0.0,
        le=100.0,
        description="The ERI score at evaluation time.",
    )
    state_before: str = Field(
        description="EndpointState before this evaluation.",
    )
    state_after: str = Field(
        description="EndpointState after this evaluation (may be unchanged).",
    )
    state_changed: bool = Field(
        description="Whether this evaluation triggered a state transition.",
    )

    # --- Strategy context ---

    strategy_used: str = Field(
        description=(
            "Which delivery strategy produced this policy: "
            "'STRATEGY_A' (static), 'STRATEGY_B' (adaptive), or 'STRATEGY_C' (AEDE)."
        ),
    )
    is_fallback: bool = Field(
        default=False,
        description=(
            "True if this policy came from a fallback tier (Redis cache or static "
            "defaults) rather than a live AEDE evaluation."
        ),
    )
    fallback_tier: int = Field(
        default=1,
        ge=1,
        le=3,
        description="Which tier produced this policy: 1=AEDE live, 2=Redis cache, 3=static fallback.",
    )

    # --- Timing ---

    evaluated_at: datetime = Field(
        description=(
            "Timestamp of evaluation. Always supplied by the caller — AEDE never "
            "reads datetime.now() internally (determinism requirement)."
        ),
    )
    window_size: int = Field(
        ge=0,
        description="Number of telemetry records in the ERI observation window.",
    )
