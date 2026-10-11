"""
FastAPI ingestion app. Data plane only -- no AEDE, no retry policy.

Run:
    .venv/bin/uvicorn relay.api.main:app --host 0.0.0.0 --port 8000
"""
from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from relay.api.demo import controller as demo_controller, demo_enabled, router as demo_router
from relay.api.endpoints import router as endpoints_router
from relay.api.events import router as events_router
from relay.api.middleware import BodySizeLimitMiddleware
from relay.api.subscriptions import router as subscriptions_router
from relay.infrastructure.messaging.producer import close as close_producer, warmup as warmup_producer


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # warmup() covers ensure_topics() + producer.start() together -- paying
    # the full cold-start cost once at boot rather than splitting it (see
    # relay/infrastructure/messaging/producer.py::warmup).
    await warmup_producer()
    yield
    await demo_controller.stop()
    await close_producer()


app = FastAPI(
    title="Relay Ingestion API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(BodySizeLimitMiddleware)
# The dashboard frontend calls the admin routes (restore, replay, demo
# scenarios) from the browser; it never writes through the dashboard backend.
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

app.include_router(events_router)
app.include_router(endpoints_router)
app.include_router(subscriptions_router)
# A control surface that reconfigures the simulator and generates traffic:
# mounted only when explicitly enabled, never by default.
if demo_enabled():
    app.include_router(demo_router)
