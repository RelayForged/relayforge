# ADR 0010: Use Pydantic v2 Frozen Models for Domain Entities and Value Objects

## Status

Accepted

## Context

The Relay Event Delivery Platform requires a standardized, immutable, and strictly validated representation for all core domain concepts (`Event`, `Endpoint`, `Subscription`, `Delivery`, `DeliveryAttempt`, `DeliveryPolicy`, `ERI`, `ExplainabilityRecord`).

The domain layer serves as the foundation across all platform boundaries:
- **Control Plane (AEDE)**: Synthesizes delivery policies deterministically based on endpoint state and ERI metrics.
- **Data Plane (Dispatcher & Workers)**: Consumes Kafka events, fetches policies, executes deliveries, and records attempt telemetry.
- **API & SDK**: Receives event ingestion payloads via FastAPI endpoints and provides a clean Python SDK interface to client applications.

Domain models in this platform must fulfill five core architectural requirements:

1. **Immutability**: The Core Architectural Law requires domain entities, policy decisions, and operational events to be strictly immutable. Mutation in memory across thread or async boundaries leads to hidden state, non-determinism, and race conditions.
2. **Strict Runtime Validation**: Payloads entering from SDKs, API requests, Kafka messages, or PostgreSQL queries must be validated at runtime interfaces to prevent malformed data from propagating into the core pipeline.
3. **High-Performance Serialization**: Fast JSON serialization/deserialization is critical for high-throughput Kafka message publishing, Redis policy caching, and REST API responses.
4. **Deterministic Policy Synthesis**: AEDE requires pure, side-effect-free, hashable, and immutable input objects to guarantee strict determinism (identical input states $\rightarrow$ identical delivery policies).
5. **Zero Schema Drift (API & SDK)**: Domain entity schemas must be shared seamlessly between the core platform, FastAPI routes, and the Python SDK without duplicating field definitions or validation logic.

### Alternatives Evaluated

#### Option 1: Python Standard Library `dataclasses`
- **Pros**: Standard library feature, zero external dependencies, standard type hints, low instantiation overhead.
- **Cons**: Lack of built-in runtime type validation (type annotations are ignored at runtime unless manual validation logic is written for every field); JSON serialization requires custom encoder helpers; no built-in schema generation (OpenAPI/JSONSchema); validation rules must be manually duplicated across API and SDK boundaries.

#### Option 2: Plain Dictionaries / `TypedDict`
- **Pros**: Zero overhead, native Python dicts, direct `json.dumps()` compatibility.
- **Cons**: No immutability guarantees (`frozen` state cannot be enforced); no runtime type checking or coercion; schema drift occurs easily across components; domain model refactoring is fragile and error-prone.

#### Option 3: Pydantic v2 (`BaseModel` with `model_config = ConfigDict(frozen=True)`)
- **Pros**:
  - **Runtime Immutability**: `frozen=True` enforces object immutability at runtime, preventing accidental field modification in workers or policy engines.
  - **Rust Core (`pydantic-core`)**: Provides high-performance C/Rust-level JSON serialization and deserialization, essential for Kafka I/O and Redis cache access.
  - **Runtime Data Validation**: Enforces strict type constraints, value bounds, and custom validators at component boundaries.
  - **FastAPI & SDK Shared Contract**: Integrates directly with FastAPI for auto-generated OpenAPI schemas and enables the Python SDK to directly import kernel entities with zero schema duplication.
  - **Hashability & Determinism**: Frozen Pydantic models can be safely hashed and passed into AEDE functions, supporting caching and deterministic policy synthesis.
- **Cons**: Adds a external library dependency (`pydantic>=2.0`) to the core kernel and Python SDK. Validation introduces minor overhead compared to unvalidated dicts (mitigated by Rust core).

## Decision

Use **Pydantic v2** frozen models (`BaseModel` with `model_config = ConfigDict(frozen=True)`) as the single source of truth for all domain entities, value objects, delivery policies, and operational events across the Relay platform (`relay/kernel/domain/`, `entities/`, `value_objects/`, `policies/`).

### Implementation Rules:
1. **Mandatory Frozen Configuration**: All domain entities and value objects must specify `model_config = ConfigDict(frozen=True)` or inherit from a base immutable model.
2. **Boundary Validation**: Serialization to and from Kafka topics, Redis caches, and PostgreSQL repositories must use Pydantic's native `.model_dump()`, `.model_dump_json()`, and `.model_validate_json()`.
3. **SDK Schema Sharing**: The Python SDK must import domain models directly from `relay.kernel.entities`, maintaining complete alignment between client payloads and server schemas.
4. **Value Objects**: Use custom frozen Pydantic types or value object wrappers (e.g. `CorrelationID`, `CausationID`, `EndpointID`) to enforce domain invariant rules at creation time.

## Consequences

### Positive
- **Guarantees Core Architectural Law**: Neither Workers nor AEDE can mutate entity state in memory after creation.
- **Ensures AEDE Determinism**: Pure, hashable, frozen models enable side-effect-free policy calculation.
- **Eliminates Schema Drift**: A field change in `relay/kernel/entities/` automatically updates FastAPI OpenAPI documentation, SDK type hints, and database mapper validation.
- **High-Speed Serialization**: Leveraging Pydantic v2's Rust backend ensures low serialization latency for Kafka messaging and Redis cache operations.
- **Static & Runtime Type Safety**: Enables static analysis tools (`mypy`, `pyright`) while protecting runtime execution against malformed payloads.

### Negative
- Introduces `pydantic` as a required dependency in both the core platform and the standalone Python SDK.
- Small runtime validation cost during object instantiation compared to unvalidated raw dictionaries.

### Mitigations
- Pin `pydantic>=2.7.0` across platform and SDK dependency specifications.
- Benchmark high-throughput ingestion and worker serialization paths to confirm performance targets are met.
