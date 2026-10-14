"""Verifying Relay webhooks on the receiving side.

A subscriber's endpoint calls `verify_signature` on every request before it
trusts the body:

    from relay.sdk import verify_signature

    @app.post("/webhook")
    async def webhook(request: Request):
        body = await request.body()
        if not verify_signature(SECRET, body, request.headers):
            raise HTTPException(401)
        ...

Standard library only, so a receiver needs nothing from the platform's
internals. It must agree with the sender in
relay/infrastructure/auth/signing.py; tests/unit/test_sdk.py checks that.
"""
import hashlib
import hmac
import time
from typing import Mapping, Optional

SIGNATURE_HEADER = "X-Relay-Signature"
TIMESTAMP_HEADER = "X-Relay-Timestamp"
NONCE_HEADER = "X-Relay-Nonce"
DEFAULT_TOLERANCE_SECONDS = 300


def _header(headers: Mapping[str, str], name: str) -> str:
    # Starlette and httpx headers are case-insensitive; a plain dict may not be.
    value = headers.get(name)
    if value is None:
        value = headers.get(name.lower())
    return value or ""


def verify_signature(
    secret: str,
    body: bytes,
    headers: Mapping[str, str],
    *,
    tolerance_seconds: Optional[int] = DEFAULT_TOLERANCE_SECONDS,
    now: Optional[float] = None,
) -> bool:
    """
    True when the request carries a valid Relay signature for `body`.

    The signature covers `timestamp.nonce.body`, so neither the body nor the
    timestamp can be changed without breaking it. Requests older (or newer)
    than `tolerance_seconds` are rejected to limit replays; pass None to skip
    that check. Compared in constant time.
    """
    signature = _header(headers, SIGNATURE_HEADER)
    timestamp = _header(headers, TIMESTAMP_HEADER)
    nonce = _header(headers, NONCE_HEADER)
    if not secret or not signature or not timestamp:
        return False

    signed = b".".join([timestamp.encode("utf-8"), nonce.encode("utf-8"), body])
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return False

    if tolerance_seconds is None:
        return True
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    current = time.time() if now is None else now
    return abs(current - sent_at) <= tolerance_seconds
