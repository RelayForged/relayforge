import asyncio
import random
import time

from fastapi import Response

from simulator.config import FailureMode, SimulatorConfig


class ConnectionResetResponse(Response):
    """
    Drops the TCP connection instead of answering.

    Returning nothing or raising would make the server send a 500, which
    the caller sees as an HTTP error, not a network fault. The connection
    is aborted through the server's transport, which uvicorn exposes on the
    object that owns `receive`. Where no transport is reachable (in-process
    test clients) this falls back to a 502 so the request still fails.
    """

    def __init__(self) -> None:
        super().__init__(content="Simulated connection reset", status_code=502)

    async def __call__(self, scope, receive, send) -> None:
        transport = getattr(getattr(receive, "__self__", None), "transport", None)
        if transport is None:
            await super().__call__(scope, receive, send)
            return
        transport.abort()
        # Let the server observe the closed connection before this returns,
        # so it does not try to write an error response to a dead socket.
        await asyncio.sleep(0)
        await asyncio.sleep(0)


def simulate_webhook(config: SimulatorConfig) -> Response:
    should_fail = (
        config.mode != FailureMode.SUCCESS and random.random() < config.rate
    )

    if should_fail:
        if config.mode == FailureMode.HTTP_ERROR:
            return Response(
                content="Simulated HTTP error",
                status_code=500,
            )

        if config.mode in (FailureMode.TIMEOUT, FailureMode.SLOW):
            time.sleep(config.latency_ms / 1000)

        if config.mode == FailureMode.RATE_LIMIT:
            return Response(
                content="Simulated rate limit",
                status_code=429,
            )

        if config.mode == FailureMode.AUTH_FAILURE:
            return Response(
                content="Simulated authentication failure",
                status_code=401,
            )

        if config.mode == FailureMode.CONNECTION_RESET:
            return ConnectionResetResponse()

        if config.mode == FailureMode.MIXED:
            # Half of the failing requests are hard errors, the other half
            # are slow successes: partial degradation rather than an outage.
            if random.random() < 0.5:
                return Response(
                    content="Simulated HTTP error",
                    status_code=500,
                )
            time.sleep(config.latency_ms / 1000)

    return Response(
        content="Webhook delivered successfully",
        status_code=200,
    )
