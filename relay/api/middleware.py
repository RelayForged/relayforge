"""
ASGI middleware for the ingestion API.

Written against the raw ASGI interface rather than Starlette's
`BaseHTTPMiddleware`: that base class runs the downstream app in a
separate anyio task per request, which adds overhead on the hot ingestion
path and complicates cancellation. This only needs to inspect headers.
"""
import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from relay.api.schemas import MAX_PAYLOAD_BYTES

# Pydantic can only bound `payload` after the whole body has been read and
# parsed into memory. This rejects the request before that happens. The
# headroom covers the JSON envelope around `payload`.
MAX_REQUEST_BYTES = MAX_PAYLOAD_BYTES + 64 * 1024


class BodySizeLimitMiddleware:
    """Reject requests whose declared or streamed body is too large."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_REQUEST_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", ()):
            if name != b"content-length":
                continue
            try:
                declared = int(value)
            except ValueError:
                await self._reject(send, 400, "Invalid Content-Length header")
                return
            if declared > self.max_bytes:
                await self._reject(
                    send, 413, f"Request body exceeds {self.max_bytes} bytes"
                )
                return
            break

        # A chunked request declares no Content-Length, so also cap while
        # streaming.
        received = 0

        async def guarded_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge()
            return message

        try:
            await self.app(scope, guarded_receive, send)
        except _BodyTooLarge:
            await self._reject(
                send, 413, f"Request body exceeds {self.max_bytes} bytes"
            )

    async def _reject(self, send: Send, status_code: int, detail: str) -> None:
        body = json.dumps({"detail": detail}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class _BodyTooLarge(Exception):
    """Internal signal from guarded_receive; never leaves this module."""
