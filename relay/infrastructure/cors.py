"""
Browser origins allowed to call the platform's HTTP services.

The API, the dashboard backend and the simulator all serve the same
dashboard frontend, so they read one setting through one function.
"""
import os

# The Vite dev server, and the built UI served by the dashboard backend.
DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173,http://127.0.0.1:5173,"
    "http://localhost:8090,http://127.0.0.1:8090"
)


def cors_origins() -> list[str]:
    """Origins from RELAY_CORS_ORIGINS (comma-separated); the two UI origins by default.

    Empty counts as unset: compose passes `${RELAY_CORS_ORIGINS:-}` through,
    and an empty allow-list would silently break the dashboard.
    """
    raw = os.environ.get("RELAY_CORS_ORIGINS") or DEFAULT_CORS_ORIGINS
    return [origin.strip() for origin in raw.split(",") if origin.strip()]
