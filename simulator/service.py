import random
import time

from fastapi import Response

from simulator.config import FailureMode, SimulatorConfig


def simulate_webhook(config: SimulatorConfig) -> Response:
    should_fail = random.random() < config.rate

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

    return Response(
        content="Webhook delivered successfully",
        status_code=200,
    )