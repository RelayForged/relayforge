# Relay Multi-Stage Application Dockerfile

# The dashboard UI, built once and served by the dashboard backend.
FROM node:22.14.0-alpine AS frontend
WORKDIR /frontend
COPY relay/dashboard/frontend/package.json relay/dashboard/frontend/package-lock.json ./
RUN npm ci
COPY relay/dashboard/frontend/ ./
RUN npm run build

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install system utilities (curl for healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

# Copy source tree
COPY . .
COPY --from=frontend /frontend/dist /app/relay/dashboard/frontend/dist

# Default entrypoint (overridden per service in docker-compose.yml)
CMD ["python", "-m", "relay.data_plane.dispatcher.main"]
