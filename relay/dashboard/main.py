from fastapi import FastAPI, WebSocket

from relay.api.chart import chart_router
from .websocket import send_state_update


app = FastAPI(title="RelayForge Dashboard")
app.include_router(chart_router)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    await send_state_update(websocket)