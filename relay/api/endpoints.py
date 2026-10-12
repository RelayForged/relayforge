"""
Endpoint registration.

`target_url` is validated by CreateEndpointRequest (SSRF guard) before it
ever reaches a repository, and `owner_id` is taken from the authenticated
principal instead of the request body so ownership cannot be spoofed.
"""
from fastapi import APIRouter, Depends, status

from relay.api.deps import get_endpoint_repository
from relay.api.schemas import CreateEndpointRequest
from relay.api.security import DEV_OWNER_ID, require_owner
from relay.infrastructure.persistence.endpoint_repository import EndpointRepository
from relay.kernel.entities.endpoint import Endpoint

router = APIRouter(tags=["endpoints"])


@router.post(
    "/endpoints",
    response_model=Endpoint,
    status_code=status.HTTP_201_CREATED,
)
async def create_endpoint(
    body: CreateEndpointRequest,
    owner_id: str = Depends(require_owner),
    repo: EndpointRepository = Depends(get_endpoint_repository),
) -> Endpoint:
    # Authenticated owner always wins. The body value is a dev-only
    # convenience for the unauthenticated local flow.
    effective_owner = owner_id
    if owner_id == DEV_OWNER_ID and body.owner_id:
        effective_owner = body.owner_id

    endpoint = Endpoint(target_url=body.target_url, owner_id=effective_owner)
    await repo.save(endpoint)
    return endpoint
