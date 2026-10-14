# Decision: Leave repeat endpoint registration as it is (T14, option 1)

- **Context:** `POST /endpoints` (`relay/api/endpoints.py::create_endpoint`) builds a new `Endpoint` with a fresh `endpoint_id` on every call, including when the same owner registers the same `target_url` again. AEDE then tracks two separate ERI scores for what is one real endpoint. The review that found the duplicate demo endpoint (T2) raised this; that demo bug was fixed separately with deterministic demo ids and does not depend on this.

- **Decision:** Option 1, leave it. An owner who registers a URL twice gets two endpoints.
  - **Why:**
    - Nothing in the demo or the MVP checklist depends on it, and it is a behaviour and schema change, not a bug fix.
    - Option 2 (return the existing endpoint, or answer 409) is only safe with a unique rule on `endpoints (owner_id, target_url)`. Existing databases already contain duplicates, so the migration would have to merge or retire them first. Deliveries and their attempts reference endpoints by id, and attempt history and telemetry are immutable facts (ADR 0014, ADR 0019), so merging two endpoint ids must not rewrite that history. Pointing old rows at a surviving id would do exactly that.
    - Registering twice is explicit client behaviour, and each registration is a distinct, visible endpoint rather than silent data loss. Returning a different id than the client asked to create would be a surprise of its own.

- **If it is revisited:** a new task with an Alembic migration that deduplicates `(owner_id, target_url)` before adding the constraint, an API behaviour decision (reuse versus 409), and tests proving no delivery attempt or telemetry row changes. Not before the MVP checklist is complete.
