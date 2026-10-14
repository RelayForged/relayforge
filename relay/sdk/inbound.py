"""Boilerplate for receiving a provider's webhook and forwarding it to Relay.

Every inbound handler (Razorpay, GitHub, ...) does the same steps. This module
owns the fixed ones so a developer writes only the two that differ:

    verify     -- is this request really from the provider?
    map_event  -- which `event_type` and `payload` should Relay carry?

Fixed steps, in order, with the status returned to the provider:

    1. body too large                      -> 413
    2. signature check fails               -> 401
    3. body is not a JSON object           -> 400
    4. mapper raises or returns bad shape  -> 422
    5. mapper returns None (not wanted)    -> 200, nothing published
    6. already forwarded (optional dedupe) -> 200, nothing published
    7. Relay did not answer 202            -> 502  (provider retries)
    8. Relay accepted                      -> 200

Only a 2xx tells the provider to stop retrying, so a 2xx is returned only
after Relay has persisted and published the event, or when there is nothing
to publish. Relay never sees the provider's original body or headers; the
mapper decides what is kept, and whatever it leaves out is lost.

`handle_inbound` has no web-framework dependency. `inbound_router` wraps it in
a FastAPI route (FastAPI is imported only when that function is called).

Not logged anywhere here: request bodies, signatures, secrets or API keys.
"""
import hashlib
import hmac
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Tuple

from relay.sdk.exceptions import RelayError

logger = logging.getLogger(__name__)

# Provider bodies are small. Relay itself caps the forwarded payload at
# 256 KiB, so reading more than this only wastes memory.
DEFAULT_MAX_BODY_BYTES = 1024 * 1024

Headers = Mapping[str, str]
Verifier = Callable[[bytes, Headers], bool]
Mapper = Callable[[Dict[str, Any], Headers], Optional[Tuple[str, Dict[str, Any]]]]
DedupeKey = Callable[[Dict[str, Any], Headers], Optional[str]]


class EventPublisher(Protocol):
    """Anything with an awaitable `publish_event`, e.g. `AsyncRelayClient`."""

    async def publish_event(self, event_type: str, payload: Dict[str, Any]) -> Any: ...


class DedupeStore(Protocol):
    """Remembers provider event ids that were already forwarded."""

    async def seen(self, key: str) -> bool: ...

    async def remember(self, key: str) -> None: ...


@dataclass(frozen=True)
class InboundResult:
    """What to send back to the provider."""

    status_code: int
    body: Dict[str, Any] = field(default_factory=dict)


class InMemoryDedupe:
    """Bounded, per-process dedupe store (oldest keys are dropped first).

    Good for one instance. Behind several replicas or across restarts,
    implement `DedupeStore` on Redis instead. `seen` then `remember` is not
    atomic, so two identical requests arriving at the same instant can both
    pass; subscribers should still deduplicate on the provider's own id.
    """

    def __init__(self, max_size: int = 10_000) -> None:
        if max_size < 1:
            raise ValueError("max_size must be at least 1")
        self._max_size = max_size
        self._keys: "OrderedDict[str, None]" = OrderedDict()

    async def seen(self, key: str) -> bool:
        return key in self._keys

    async def remember(self, key: str) -> None:
        self._keys[key] = None
        self._keys.move_to_end(key)
        while len(self._keys) > self._max_size:
            self._keys.popitem(last=False)


def hmac_sha256_hex(secret: str, header: str, prefix: str = "") -> Verifier:
    """Verifier for the common "HMAC-SHA256 of the raw body, hex" scheme.

    Razorpay: `hmac_sha256_hex(secret, "X-Razorpay-Signature")`.
    GitHub-style `sha256=<hex>`:
              `hmac_sha256_hex(secret, "X-Hub-Signature-256", prefix="sha256=")`.

    Schemes that sign a timestamp as well (Stripe, for one) are different and
    need their own verifier; pass any `(raw_body, headers) -> bool` callable.
    An empty secret is rejected up front, because it would accept forgeries.
    """
    if not secret:
        raise ValueError("secret must not be empty")
    key = secret.encode("utf-8")
    header_name = header.lower()

    def verify(raw_body: bytes, headers: Headers) -> bool:
        received = headers.get(header_name)
        if not received:
            return False
        if prefix:
            if not received.startswith(prefix):
                return False
            received = received[len(prefix):]
        expected = hmac.new(key, raw_body, hashlib.sha256).hexdigest()
        # Bytes, so a non-ASCII header value cannot raise inside compare_digest.
        return hmac.compare_digest(expected.encode("utf-8"), received.strip().encode("utf-8"))

    return verify


async def handle_inbound(
    raw_body: bytes,
    headers: Headers,
    *,
    verify: Verifier,
    map_event: Mapper,
    relay: EventPublisher,
    dedupe_key: Optional[DedupeKey] = None,
    dedupe: Optional[DedupeStore] = None,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> InboundResult:
    """Run the fixed inbound steps for one provider request.

    Args:
        raw_body: The exact bytes received. Pass them untouched; the provider
            signed those bytes, and re-serialised JSON would not match.
        headers: Request headers (names are matched case-insensitively).
        verify: `(raw_body, headers) -> bool`.
        map_event: `(parsed_body, headers) -> (event_type, payload)`, or
            `None` to ignore this event.
        relay: Publisher, normally an `AsyncRelayClient`.
        dedupe_key: Optional `(parsed_body, headers) -> provider event id`.
            Needs `dedupe`. A `None` key skips the check for that request.
        dedupe: Store used with `dedupe_key`. A key is remembered only after
            Relay accepts the event, so a failed publish is retried, not dropped.
        max_body_bytes: Larger bodies are rejected with 413.
    """
    if dedupe_key is not None and dedupe is None:
        raise ValueError("dedupe_key requires a dedupe store")

    if len(raw_body) > max_body_bytes:
        return InboundResult(413, {"detail": "Request body too large"})

    lowered = {name.lower(): value for name, value in headers.items()}

    try:
        authentic = verify(raw_body, lowered)
    except Exception:
        # A broken verifier must fail closed.
        logger.exception("Inbound signature verifier raised")
        authentic = False
    if not authentic:
        return InboundResult(401, {"detail": "Invalid signature"})

    try:
        body = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        return InboundResult(400, {"detail": "Body is not valid JSON"})
    if not isinstance(body, dict):
        return InboundResult(400, {"detail": "Body must be a JSON object"})

    try:
        mapped = map_event(body, lowered)
        key = dedupe_key(body, lowered) if dedupe_key is not None else None
    except Exception:
        # Usually a provider body shape the mapper does not expect.
        logger.exception("Inbound mapper raised")
        return InboundResult(422, {"detail": "Could not map the request to a Relay event"})

    if mapped is None:
        return InboundResult(200, {"ok": True, "ignored": True})

    if not _is_valid_mapping(mapped):
        logger.error("Inbound mapper returned an invalid (event_type, payload) pair")
        return InboundResult(422, {"detail": "Mapper must return (event_type, payload dict)"})
    event_type, payload = mapped

    if key is not None and dedupe is not None and await dedupe.seen(key):
        return InboundResult(200, {"ok": True, "duplicate": True})

    try:
        accepted = await relay.publish_event(event_type, payload)
    except RelayError as exc:
        # Includes Relay rejecting our API key (401) or the payload (422):
        # neither is fixed by the provider retrying, but a 2xx would hide
        # the loss, so stay non-2xx and let the log say why.
        logger.warning("Relay did not accept the event (%s)", type(exc).__name__)
        return InboundResult(502, {"detail": "Relay did not accept the event"})

    if key is not None and dedupe is not None:
        await dedupe.remember(key)

    return InboundResult(
        200, {"ok": True, "event_id": getattr(accepted, "event_id", None)}
    )


def _is_valid_mapping(mapped: Any) -> bool:
    if not isinstance(mapped, tuple) or len(mapped) != 2:
        return False
    event_type, payload = mapped
    return isinstance(event_type, str) and bool(event_type) and isinstance(payload, dict)


def inbound_router(
    *,
    path: str,
    verify: Verifier,
    map_event: Mapper,
    relay: EventPublisher,
    dedupe_key: Optional[DedupeKey] = None,
    dedupe: Optional[DedupeStore] = None,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    tags: Optional[List[str]] = None,
) -> Any:
    """A FastAPI `APIRouter` with one `POST {path}` route running `handle_inbound`.

    When `dedupe_key` is given without a `dedupe` store, a per-process
    `InMemoryDedupe` is used. Requires FastAPI, imported here so the rest of
    the SDK stays dependency-light.
    """
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse

    if dedupe_key is not None and dedupe is None:
        dedupe = InMemoryDedupe()

    router = APIRouter(tags=tags or ["inbound-webhooks"])

    @router.post(path)
    async def receive(request: Request) -> JSONResponse:
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > max_body_bytes:
            result = InboundResult(413, {"detail": "Request body too large"})
        else:
            result = await handle_inbound(
                await request.body(),
                dict(request.headers),
                verify=verify,
                map_event=map_event,
                relay=relay,
                dedupe_key=dedupe_key,
                dedupe=dedupe,
                max_body_bytes=max_body_bytes,
            )
        return JSONResponse(content=result.body, status_code=result.status_code)

    return router
