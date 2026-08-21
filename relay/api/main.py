"""
FastAPI ingestion app. Data plane only -- no AEDE, no retry policy.

Run:
    .venv/bin/uvicorn relay.api.main:app --host 0.0.0.0 --port 8000
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from relay.api.endpoints import router as endpoints_router
from relay.api.events import router as events_router
from relay.api.subscriptions import router as subscriptions_router
from relay.infrastructure.messaging.producer import close as close_producer


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await close_producer()


app = FastAPI(
    title="Relay Ingestion API",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(events_router)
app.include_router(endpoints_router)
app.include_router(subscriptions_router)
