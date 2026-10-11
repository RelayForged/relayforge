"""
Endpoint registration.

`target_url` is validated by CreateEndpointRequest (SSRF guard) before it
ever reaches a repository, and `owner_id` is taken from the authenticated
principal instead of the request body so ownership cannot be spoofed.
"""
import json
from fastapi import APIRouter, Depends, HTTPException, status

from relay.api.deps import get_endpoint_repository
from relay.api.schemas import CreateEndpointRequest, RestoreEndpointResponse
from relay.api.security import DEV_OWNER_ID, require_owner
from relay.infrastructure.messaging.admin import EVENTS_INTERNAL_ENDPOINT_TOPIC
from relay.infrastructure.messaging.producer import publish
from relay.infrastructure.persistence.endpoint_repository import EndpointRepository
from relay.kernel.entities.endpoint import Endpoint

router = APIRouter(tags=["endpoints"])


async def publish_restore(endpoint_id: str) -> None:
    """Ask the Control Plane to move a QUARANTINED endpoint to RECOVERING."""
    payload = json.dumps({
        "action": "restore",
        "endpoint_id": endpoint_id,
    }).encode("utf-8")
    await publish(
        EVENTS_INTERNAL_ENDPOINT_TOPIC,
        key=endpoint_id,
        value=payload,
    )


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


@router.get(
    "/endpoints/{endpoint_id}",
    response_model=Endpoint,
    status_code=status.HTTP_200_OK,
)
async def get_endpoint(
    endpoint_id: str,
    owner_id: str = Depends(require_owner),
    repo: EndpointRepository = Depends(get_endpoint_repository),
) -> Endpoint:
    endpoint = await repo.get_by_id(endpoint_id)
    if endpoint is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Endpoint {endpoint_id} not found",
        )
    if owner_id != DEV_OWNER_ID and endpoint.owner_id != owner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this endpoint",
        )
    return endpoint


@router.post(
    "/endpoints/{endpoint_id}/restore",
    response_model=RestoreEndpointResponse,
    status_code=status.HTTP_200_OK,
)
async def restore_endpoint(
    endpoint_id: str,
    owner_id: str = Depends(require_owner),
    repo: EndpointRepository = Depends(get_endpoint_repository),
) -> RestoreEndpointResponse:
    endpoint = await repo.get_by_id(endpoint_id)
    if endpoint is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Endpoint {endpoint_id} not found",
        )
    if owner_id != DEV_OWNER_ID and endpoint.owner_id != owner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to restore this endpoint",
        )

    try:
        await publish_restore(endpoint_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "Publishing restore message to Kafka failed.",
                "endpoint_id": endpoint_id,
            },
        ) from exc

    return RestoreEndpointResponse(
        status="RESTORE_REQUESTED",
        endpoint_id=endpoint_id,
        target_state="RECOVERING",
    )

