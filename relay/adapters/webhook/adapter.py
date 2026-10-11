"""
HTTP Webhook Delivery Adapter (v1).

The only place outbound webhook HTTP is performed. Translates httpx
outcomes into the transport-neutral DeliveryAdapter contract so the
Worker never sees an httpx type.
"""
import os
from typing import Mapping, Optional

import httpx

from relay.kernel.interfaces.delivery_adapter import (
    DeliveryResponse,
    DeliveryTimeout,
    DeliveryTransportError,
)

DEFAULT_TIMEOUT_SECONDS = 5.0


def _default_timeout_seconds() -> float:
    raw = os.environ.get("RELAY_WEBHOOK_TIMEOUT_SECONDS")
    if raw is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        val = float(raw)
        return val if val > 0 else DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


class WebhookDeliveryAdapter:
    def __init__(
        self,
        *,
        timeout_seconds: Optional[float] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._timeout = httpx.Timeout(
            timeout_seconds if timeout_seconds is not None else _default_timeout_seconds()
        )
        self._client = client
        self._owns_client = client is None

    async def deliver(
        self, target_url: str, body: bytes, headers: Mapping[str, str]
    ) -> DeliveryResponse:
        try:
            response = await self._get_client().post(
                target_url, content=body, headers=dict(headers)
            )
        except httpx.TimeoutException as exc:
            raise DeliveryTimeout("client timeout") from exc
        except (httpx.HTTPError, httpx.InvalidURL, httpx.CookieConflict) as exc:
            # httpx.InvalidURL does not inherit from HTTPError, so it used
            # to escape and take the Dispatcher down with it.
            raise DeliveryTransportError(f"{type(exc).__name__}: {exc}") from exc
        return DeliveryResponse(status_code=response.status_code)

    def _get_client(self) -> httpx.AsyncClient:
        """
        One pooled client for the process. Building an AsyncClient per
        attempt meant a fresh connection and TLS handshake every delivery,
        and that setup cost was being recorded as endpoint latency_ms.

        Redirects stay disabled: following them is an SSRF bypass.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=False,
                limits=httpx.Limits(
                    max_connections=100,
                    max_keepalive_connections=20,
                ),
            )
        return self._client

    async def aclose(self) -> None:
        """Release the pooled client. Injected clients are left alone."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
