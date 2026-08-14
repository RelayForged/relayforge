from fastapi import FastAPI

from simulator.config import SimulatorConfig
from simulator.service import simulate_webhook
from simulator.state import SimulatorState

app = FastAPI(title="RelayForge Endpoint Simulator")

simulator_state = SimulatorState()


@app.post("/simulator/config")
def update_config(config: SimulatorConfig) -> SimulatorConfig:
    simulator_state.config = config
    return simulator_state.config


@app.post("/webhook")
def webhook():
    return simulate_webhook(simulator_state.config)