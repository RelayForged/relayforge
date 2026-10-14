"""
Delivery Policy — the immutable contract between the Control Plane and the Data Plane.

A DeliveryPolicy answers every operational question before execution begins.
Workers never ask "should I retry?" or "should I pause?" — the answer already
exists in the policy object they receive.

Produced by the Policy Synthesizer (relay/control_plane/aede/synthesizer.py)
or by a static/adaptive strategy fallback. Consumed by the Dispatcher and Workers.

See docs/architecture.md § Delivery Policy Model for the full specification.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Sub-policy value objects
# ---------------------------------------------------------------------------


class BackoffStrategy(str, Enum):
    """Backoff algorithm for retry scheduling."""

    FIXED = "FIXED"
    """Constant delay between retries."""

    LINEAR = "LINEAR"
    """Delay increases linearly: delay = initial_delay * attempt_number."""

    EXPONENTIAL = "EXPONENTIAL"
    """Delay doubles each attempt: delay = initial_delay * 2^(attempt - 1), capped at max_delay."""


class IsolationAction(str, Enum):
    """
    Isolation posture for an endpoint.

    Maps directly to EndpointState semantics but expressed as an action
    rather than a state — because the policy tells the worker *what to do*,
    not *what the endpoint is*.
    """

    CONTINUE = "CONTINUE"
    """Normal delivery. No restrictions."""

    REDUCE_CONCURRENCY = "REDUCE_CONCURRENCY"
    """Reduce parallel deliveries to this endpoint."""

    PAUSE = "PAUSE"
    """
    Throttled delivery for an UNSTABLE endpoint: one at a time under the
    policy's long backoff. Not a stop -- the attempts that still go out are
    the only evidence the endpoint can recover on (ADR 0022). Only
    QUARANTINE stops HTTP.
    """

    QUARANTINE = "QUARANTINE"
    """Full isolation. No delivery attempts. Events preserved for replay."""


class AlertLevel(str, Enum):
    """Severity level for operational notifications."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class RetryPolicy(BaseModel):
    """
    Defines how a failed delivery should be retried.

    All intervals are in seconds. The Worker reads these values and schedules
    retries accordingly — it never computes intervals itself.
    """

    model_config = ConfigDict(frozen=True)

    initial_delay_seconds: float = Field(
        gt=0,
        description="Delay before the first retry attempt, in seconds.",
    )
    max_delay_seconds: float = Field(
        gt=0,
        description="Upper bound on retry delay. No retry waits longer than this.",
    )
    max_retries: int = Field(
        ge=0,
        description="Maximum number of retry attempts. 0 means no retries.",
    )
    backoff_strategy: BackoffStrategy = Field(
        description="Algorithm for computing successive retry delays.",
    )
    backoff_multiplier: float = Field(
        default=2.0,
        gt=0,
        description=(
            "Multiplier applied per retry step. For EXPONENTIAL: delay *= multiplier. "
            "For LINEAR: delay += initial_delay. For FIXED: ignored."
        ),
    )
    deadline_seconds: Optional[float] = Field(
        default=None,
        gt=0,
        description=(
            "Absolute time limit for all retries combined. If set, retries stop "
            "after this many seconds regardless of max_retries. None means no deadline."
        ),
    )
    jitter_ratio: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Random jitter applied as a fraction of the computed retry delay. "
            "0.0 means no jitter (deterministic). 0.25 means ±25% of the delay. "
            "Set by the Control Plane (Policy Synthesizer / Strategy); consumed by "
            "the Data Plane (Retry Manager). This field exists so that jitter is a "
            "policy decision, not a data-plane invention (see ADR 0003, ADR 0005)."
        ),
    )


class ConcurrencyPolicy(BaseModel):
    """
    Controls parallel delivery to an endpoint.

    Degraded endpoints get reduced concurrency to avoid overwhelming them
    during recovery. Healthy endpoints can receive multiple deliveries
    simultaneously.
    """

    model_config = ConfigDict(frozen=True)

    max_parallel: int = Field(
        ge=1,
        description="Maximum number of concurrent delivery attempts to this endpoint.",
    )
    queue_priority: int = Field(
        default=0,
        ge=0,
        description=(
            "Priority hint for queue ordering. Higher values = higher priority. "
            "Used by the Dispatcher to order work assignment."
        ),
    )
    throughput_limit: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Maximum deliveries per minute to this endpoint. None means unlimited. "
            "Used to respect rate limits on the receiving side."
        ),
    )


class IsolationPolicy(BaseModel):
    """
    Defines the isolation posture for an endpoint.

    The isolation action maps to EndpointState semantics but is expressed
    as a worker instruction — what to *do*, not what the endpoint *is*.
    """

    model_config = ConfigDict(frozen=True)

    action: IsolationAction = Field(
        description="Current isolation action for this endpoint.",
    )


class ReplayPolicy(BaseModel):
    """
    Controls replay behavior for queued/failed events.

    Replay creates new DeliveryAttempts from historical events.
    It never mutates history — each replay is a fresh attempt.
    """

    model_config = ConfigDict(frozen=True)

    allowed: bool = Field(
        default=False,
        description="Whether replay is currently permitted for this endpoint.",
    )
    batch_size: int = Field(
        default=50,
        ge=1,
        description="Number of events to replay in a single batch.",
    )
    speed: float = Field(
        default=1.0,
        gt=0,
        description=(
            "Replay speed multiplier. 1.0 = normal rate. Values < 1.0 slow replay "
            "to avoid overwhelming a recovering endpoint."
        ),
    )


class NotificationPolicy(BaseModel):
    """
    Controls operational notifications triggered by policy decisions.

    Notifications are informational — they never affect delivery behavior.
    """

    model_config = ConfigDict(frozen=True)

    notify_admin: bool = Field(
        default=False,
        description="Whether to send an administrative notification.",
    )
    alert_level: AlertLevel = Field(
        default=AlertLevel.INFO,
        description="Severity of the notification.",
    )
    suppress: bool = Field(
        default=False,
        description=(
            "If True, suppress notifications even if notify_admin is True. "
            "Used to prevent notification storms during sustained outages."
        ),
    )


class IntegrityPolicy(BaseModel):
    """
    Controls request signing and integrity verification.

    MVP: HMAC signing placeholder. Full implementation deferred.
    """

    model_config = ConfigDict(frozen=True)

    hmac_required: bool = Field(
        default=False,
        description="Whether outbound requests must be HMAC-signed.",
    )
    signature_ttl_seconds: Optional[int] = Field(
        default=None,
        gt=0,
        description="How long the HMAC signature is valid, in seconds.",
    )
    replay_protection: bool = Field(
        default=False,
        description="Whether to include a nonce for replay attack prevention.",
    )


# ---------------------------------------------------------------------------
# DeliveryPolicy — the composite immutable contract
# ---------------------------------------------------------------------------


class DeliveryPolicy(BaseModel):
    """
    An immutable operational contract produced by the Control Plane (AEDE or
    a fallback strategy) and consumed by the Data Plane (Dispatcher + Workers).

    A single DeliveryPolicy answers every question a Worker needs:
    - How should I retry?           → retry_policy
    - How many parallel attempts?   → concurrency_policy
    - Should I deliver at all?      → isolation_policy
    - Can I replay queued events?   → replay_policy
    - Should I notify anyone?       → notification_policy
    - Do I need to sign requests?   → integrity_policy

    Workers never interpret these fields. They execute them mechanically.
    Decision logic belongs exclusively in the Control Plane.
    """

    model_config = ConfigDict(frozen=True)

    # --- Policy Metadata ---

    policy_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Globally unique identifier for this policy instance.",
    )
    version: int = Field(
        default=1,
        ge=1,
        description=(
            "Policy schema version. Workers ignore unknown fields, allowing "
            "policy evolution without breaking execution components."
        ),
    )
    endpoint_id: str = Field(
        description="The endpoint this policy applies to.",
    )
    endpoint_state: str = Field(
        description="The EndpointState at the time this policy was synthesized.",
    )
    eri_score: float = Field(
        ge=0.0,
        le=100.0,
        description="The ERI score at the time this policy was synthesized.",
    )
    computed_at: datetime = Field(
        description=(
            "When this policy was produced. Always supplied by the caller — "
            "AEDE never reads datetime.now() internally."
        ),
    )

    # --- Sub-policies ---

    retry_policy: RetryPolicy
    concurrency_policy: ConcurrencyPolicy
    isolation_policy: IsolationPolicy
    replay_policy: ReplayPolicy = Field(default_factory=lambda: ReplayPolicy())
    notification_policy: NotificationPolicy = Field(
        default_factory=lambda: NotificationPolicy()
    )
    integrity_policy: IntegrityPolicy = Field(
        default_factory=lambda: IntegrityPolicy()
    )
