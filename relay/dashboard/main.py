from fastapi import FastAPI, WebSocket


app = FastAPI(title="RelayForge Dashboard")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_text("RelayForge Dashboard connected")