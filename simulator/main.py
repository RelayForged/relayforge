import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from simulator.config import SimulatorConfig
from simulator.service import simulate_webhook
from simulator.state import SimulatorState

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


@app.post("/simulator/config")
def update_config(config: SimulatorConfig) -> SimulatorConfig:
    simulator_state.config = config
    return simulator_state.config


@app.get("/simulator/config")
def read_config() -> SimulatorConfig:
    return simulator_state.config


@app.post("/webhook")
def webhook():
    return simulate_webhook(simulator_state.config)
