"""Endpoint registration. Week 2: URL + owner_id only; is_active defaults True."""
from fastapi import APIRouter, Depends, status

from relay.api.deps import get_endpoint_repository
from relay.api.schemas import CreateEndpointRequest
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
    repo: EndpointRepository = Depends(get_endpoint_repository),
) -> Endpoint:
    endpoint = Endpoint(target_url=body.target_url, owner_id=body.owner_id)
    await repo.save(endpoint)
    return endpoint
