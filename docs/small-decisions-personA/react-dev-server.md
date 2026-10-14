# Decision: No Compose service for the React frontend

- **Context:** Nothing in `docker-compose.yml` serves the React build, and the frontend WebSocket URL still points at port 8000 (the API) instead of 8090 (the dashboard backend).

- **Decision:** Do not add a frontend service. The frontend runs on the host with the Vite dev server on port 5173 (`npm run dev` in `relay/dashboard/frontend`). CORS already allows that origin: `relay/infrastructure/cors.py` defaults `RELAY_CORS_ORIGINS` to `http://localhost:5173,http://127.0.0.1:5173`, and the API, dashboard and simulator all mount it.
  - **Why:** the frontend is under active change by Person C, and a container would rebuild or go stale on every edit. The dev server already works against the compose backend with no further wiring.

- **Not part of this decision:** the WebSocket URL fix (`ws://127.0.0.1:8090/ws`) is a one-line change in `App.jsx`, which is Person C's file.

- **If it is revisited:** serving a production build from the dashboard backend or a small static container would need a decision on origin and CORS, and would belong with the packaging work.
