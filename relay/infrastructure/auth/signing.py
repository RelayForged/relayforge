"""
HMAC-SHA256 signing for outbound webhook requests.

One implementation shared by the sender (the Worker) and any receiver
that verifies (the Endpoint Simulator), so the two cannot drift apart.

The signed material is `timestamp.nonce.body`. The timestamp and nonce
are inside the signature, otherwise replay protection would be a header
an attacker can rewrite. Whether a request is signed at all is not
decided here: that is `DeliveryPolicy.integrity_policy`, set by the
Control Plane.
"""
import hashlib
import hmac
import os
from datetime import datetime
from typing import Optional

SIGNING_SECRET_ENV = "RELAY_WEBHOOK_SIGNING_SECRET"
HMAC_REQUIRED_ENV = "RELAY_HMAC_REQUIRED"

SIGNATURE_HEADER = "X-Relay-Signature"
TIMESTAMP_HEADER = "X-Relay-Timestamp"
NONCE_HEADER = "X-Relay-Nonce"
SIGNATURE_PREFIX = "sha256="
DEFAULT_SIGNATURE_TTL_SECONDS = 300


def signing_secret_from_env() -> str:
    return os.environ.get(SIGNING_SECRET_ENV, "")


def hmac_required_from_env() -> bool:
    """
    Deployment-level switch for request signing. Read once at process
    start and handed to the Control Plane as an explicit input; policy
    synthesis itself never reads the environment.
    """
    return os.environ.get(HMAC_REQUIRED_ENV, "false").lower() in ("true", "1", "yes")


def compute_relay_signature(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    """The `X-Relay-Signature` header value for a request."""
    signed = b".".join([timestamp.encode("utf-8"), nonce.encode("utf-8"), body])
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def verify_relay_signature(
    secret: str,
    *,
    timestamp: str,
    nonce: str,
    body: bytes,
    signature: str,
    now: datetime,
    max_age_seconds: Optional[int] = DEFAULT_SIGNATURE_TTL_SECONDS,
) -> bool:
    """
    True when `signature` matches and the timestamp is within
    `max_age_seconds` of `now` (either direction, to tolerate clock skew).
    Pass `max_age_seconds=None` to skip the freshness check.

    Compared in constant time so the digest cannot be recovered by timing.
    """
    if not secret or not signature:
        return False
    expected = compute_relay_signature(secret, timestamp, nonce, body)
    if not hmac.compare_digest(expected, signature):
        return False
    if max_age_seconds is None:
        return True
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    return abs(now.timestamp() - sent_at) <= max_age_seconds
