# ADR 0006: Use Python and FastAPI for the API Layer

## Status

Accepted

## Context

The platform requires a web framework for:
- The Event Ingestion API (public-facing, receives events from SDKs)
- The Administrative API (manage endpoints, subscriptions, replay)
- The Dashboard API (read-only aggregation for the dashboard)

The choice of language and framework affects:
- SDK consistency (the Python SDK and the API server can share domain models)
- Developer onboarding (a research project benefits from accessible tooling)
- Async performance (the ingestion path must handle concurrent event publication without blocking)
- Documentation (auto-generated OpenAPI documentation reduces maintenance overhead)
- Ecosystem (libraries for Kafka, PostgreSQL, Redis, Prometheus must be available)

### Framework Options Evaluated

**FastAPI (Python)** — Modern, async-native Python framework. Auto-generates OpenAPI documentation from type annotations. Excellent ecosystem for data-intensive applications (Pydantic, SQLAlchemy, aiokafka, prometheus-client). Widely adopted in data engineering and research environments.

**Django REST Framework (Python)** — Mature, batteries-included framework. Primarily synchronous, though ASGI support exists. Heavier than needed for this project. ORM coupling makes it harder to maintain the clean domain model the platform requires.

**Flask (Python)** — Lightweight and flexible but requires significant boilerplate for async handling, request validation, and OpenAPI documentation. Does not encourage domain-driven design.

**Go + Chi/Gin** — High performance, excellent concurrency model, small binary size. However, Go's type system makes it harder to share data models between the API layer and a Python SDK. Research codebases benefit from Python's expressiveness and ecosystem. The performance difference is not material for this project's throughput requirements.

**Node.js + Express/Fastify** — Event-loop model handles concurrency well. TypeScript provides type safety. However, Python is more natural for the research community, and the SDK would be in Python regardless.

## Decision

Use **Python 3.11+** with **FastAPI** as the API framework.

FastAPI is chosen because:

1. **Async-native**: The ingestion API must handle concurrent event submissions without blocking. FastAPI's async request handling (via `asyncio` and `uvicorn`) provides this without additional concurrency infrastructure.

2. **Pydantic integration**: All API request and response models are defined as Pydantic v2 schemas. These schemas enforce validation, generate OpenAPI documentation automatically, and can be shared with the Python SDK — ensuring the SDK and API speak the same data model.

3. **Python SDK consistency**: The Python SDK is the primary developer interface. Sharing domain models, validation logic, and type definitions between the SDK and the API server avoids duplication and drift.

4. **OpenAPI auto-generation**: FastAPI generates complete, accurate OpenAPI 3.x documentation from route and model definitions. This documentation is served at `/docs` and `/redoc` without any manual maintenance.

5. **Research community alignment**: The platform is a research artifact. Python is the dominant language in both the software research community and the distributed systems space. Reviewers, contributors, and future researchers are most likely to be Python-proficient.

6. **Ecosystem**: `aiokafka`, `asyncpg`, `redis-py`, `prometheus-client`, `httpx` — every infrastructure dependency has a high-quality async Python library.

## Consequences

**Positive:**
- SDK and API share Pydantic models. A type change propagates consistently.
- OpenAPI documentation is always in sync with the implementation.
- Async I/O handles the burst event ingestion path without thread pool pressure.
- Type annotations enable static analysis (`mypy`, `pyright`), reducing runtime bugs.
- FastAPI's dependency injection system cleanly supports the platform's layered architecture (repositories, services, adapters injected per request).

**Negative:**
- Python has higher memory overhead and lower raw throughput than Go or Rust for CPU-bound workloads. This is not a concern here — the bottleneck is Kafka I/O and database writes, not CPU.
- Python's GIL limits true parallelism within a single process. Mitigated by running multiple uvicorn worker processes or deploying multiple pods in Kubernetes.
- The async programming model introduces complexity (async/await propagation, event loop management). Team members unfamiliar with async Python have a learning curve.

**Mitigations:**
- Use `uvicorn` with multiple workers in production to work around GIL limitations.
- Kubernetes horizontal scaling handles throughput requirements at the infrastructure level.
- AEDE's pure computation logic (ERI, state machine) runs synchronously and benefits from Python's readability without async complexity.

Python and FastAPI are the correct choice for a developer-first, research-oriented platform where consistency between SDK and API, documentation quality, and community accessibility outweigh raw throughput considerations.
