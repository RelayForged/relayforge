from fastapi import FastAPI, WebSocket

from .websocket import send_state_update


app = FastAPI(title="RelayForge Dashboard")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    await send_state_update(websocket)