from datetime import datetime, timezone
import logging
import os
from typing import Any, Dict, List

from fastapi import FastAPI, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from relay.infrastructure.auth.signing import (
    NONCE_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    signing_secret_from_env,
    verify_relay_signature,
)
from simulator.config import SimulatorConfig
from simulator.service import ConnectionResetResponse, simulate_webhook
from simulator.state import SimulatorState

# uvicorn only configures its own loggers; without this the verification
# lines below would be dropped at the default WARNING level.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("simulator")

app = FastAPI(title="RelayForge Endpoint Simulator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.environ.get(
            "RELAY_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",")
        if origin.strip()
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

simulator_state = SimulatorState()

VERIFIED = "verified"
INVALID = "invalid"
UNSIGNED = "unsigned"
UNVERIFIABLE = "unverifiable"


@app.post("/simulator/config")
def update_config(config: SimulatorConfig) -> SimulatorConfig:
    simulator_state.config = config
    return simulator_state.config


@app.get("/simulator/config")
def read_config() -> SimulatorConfig:
    return simulator_state.config


@app.get("/simulator/deliveries")
def read_deliveries(limit: int = Query(default=50, ge=1, le=200)) -> List[Dict[str, Any]]:
    """Requests this receiver saw, newest first, with the signature check result."""
    return list(simulator_state.received)[-limit:][::-1]


def _signature_status(request: Request, body: bytes) -> str:
    """
    Verify like a real subscriber would: against the shared secret, over
    the exact bytes received, with the timestamp checked for freshness.
    """
    signature = request.headers.get(SIGNATURE_HEADER)
    if signature is None:
        return UNSIGNED
    secret = signing_secret_from_env()
    if not secret:
        return UNVERIFIABLE
    valid = verify_relay_signature(
        secret,
        timestamp=request.headers.get(TIMESTAMP_HEADER, ""),
        nonce=request.headers.get(NONCE_HEADER, ""),
        body=body,
        signature=signature,
        now=datetime.now(timezone.utc),
    )
    return VERIFIED if valid else INVALID


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.body()
    attempt_id = request.headers.get("X-Relay-Attempt-Id")
    signature_status = _signature_status(request, body)

    if signature_status == INVALID:
        logger.warning("SIGNATURE INVALID attempt_id=%s", attempt_id)
        response: Response = Response(content="Invalid signature", status_code=401)
    else:
        if signature_status == VERIFIED:
            logger.info("SIGNATURE VERIFIED attempt_id=%s", attempt_id)
        # simulate_webhook sleeps to model slow endpoints; keep it off the loop.
        response = await run_in_threadpool(simulate_webhook, simulator_state.config)

    signature = request.headers.get(SIGNATURE_HEADER)
    simulator_state.received.append(
        {
            "attempt_id": attempt_id,
            "event_id": request.headers.get("X-Relay-Event-Id"),
            "received_at": datetime.now(timezone.utc).isoformat(),
            # Enough of the digest to recognise it, not the whole value.
            "signature": f"{signature[:23]}…" if signature else None,
            "signature_status": signature_status,
            "response_status": (
                None
                if isinstance(response, ConnectionResetResponse)
                else response.status_code
            ),
        }
    )
    return response
