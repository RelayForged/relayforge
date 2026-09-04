"""
Egress guard for outbound webhook targets (CWE-918).

`Endpoint.target_url` is an arbitrary client-supplied string and the
webhook Worker is the process that dereferences it, so an unguarded
target turns the data plane into an SSRF proxy for the cluster's internal
network and cloud metadata service.

The guard runs twice on purpose:

- `check_url()` at registration (`relay/api/schemas.py`) — cheap, no DNS.
- `assert_deliverable()` immediately before every POST
  (`relay/data_plane/workers/webhook.py`) — resolves the hostname,
  because a name that pointed somewhere public at registration time can
  be re-pointed at 169.254.169.254 afterwards (DNS rebinding).

The Endpoint entity is frozen kernel (ADR 0010) and cannot carry a
validator, and the kernel is read-only for the data plane, so validation
lives here instead of on the entity.

`RELAY_ALLOW_PRIVATE_TARGETS=true` disables the address checks so the
local Simulator on 127.0.0.1 is reachable in dev. Never enable it in a
deployment that accepts untrusted endpoint registrations.
"""
from __future__ import annotations

import asyncio
import os
import socket
from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Names that resolve to link-local metadata endpoints on the major clouds,
# plus loopback aliases that are not IP literals.
BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
    }
)

MAX_TARGET_URL_LENGTH = 2048

# Ranges the ipaddress module does not flag through any is_* property, so
# they have to be listed explicitly. RFC 6598 carrier-grade NAT space is
# not `is_private` on CPython 3.12, yet 100.100.100.200 is Alibaba
# Cloud's metadata endpoint and the range is widely used for internal
# infrastructure. IPv4-mapped, NAT64 and 6to4 forms of loopback/metadata
# are already caught by is_private / is_reserved.
BLOCKED_NETWORKS = (
    ip_network("100.64.0.0/10"),
)


class UnsafeTargetURL(ValueError):
    """A webhook target is malformed or resolves somewhere it must not."""


def private_targets_allowed() -> bool:
    return os.environ.get("RELAY_ALLOW_PRIVATE_TARGETS", "false").lower() in (
        "true",
        "1",
        "yes",
    )


def _reject_non_public(candidate: str) -> None:
    """Raise if `candidate` is an IP literal outside public unicast space."""
    # getaddrinfo returns IPv6 link-local addresses with a scope suffix.
    try:
        addr = ip_address(candidate.split("%", 1)[0])
    except ValueError:
        return

    # Unpack embedded IPv4 in IPv6 (e.g. 6to4 2002::/16 or IPv4-mapped ::ffff:0:0/96)
    if addr.version == 6:
        embedded_v4 = getattr(addr, "sixtofour", None) or getattr(addr, "ipv4_mapped", None)
        if embedded_v4 is not None:
            _reject_non_public(str(embedded_v4))

    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    ):
        raise UnsafeTargetURL(f"target resolves to non-public address {candidate}")
    if any(addr in network for network in BLOCKED_NETWORKS):
        raise UnsafeTargetURL(f"target resolves to blocked network {candidate}")


def check_url(url: str) -> str:
    """
    Validate scheme and host without touching DNS. Returns the host.

    Raises UnsafeTargetURL so callers can map it to 422 (registration) or
    to a failed DeliveryAttempt (delivery).
    """
    if len(url) > MAX_TARGET_URL_LENGTH:
        raise UnsafeTargetURL(
            f"target_url exceeds {MAX_TARGET_URL_LENGTH} characters"
        )
    parts = urlsplit(url)
    if parts.scheme not in ALLOWED_SCHEMES:
        raise UnsafeTargetURL("target_url must use http or https")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeTargetURL("target_url must include a host")
    if parts.username or parts.password:
        raise UnsafeTargetURL("target_url must not embed credentials")
    if private_targets_allowed():
        return host
    if host in BLOCKED_HOSTNAMES:
        raise UnsafeTargetURL(f"target_url host {host!r} is not permitted")
    _reject_non_public(host)
    return host


async def assert_deliverable(url: str) -> None:
    """`check_url` plus a DNS check on every resolved address."""
    host = check_url(url)
    if private_targets_allowed():
        return
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeTargetURL(f"target_url host {host!r} does not resolve") from exc
    for info in infos:
        _reject_non_public(str(info[4][0]))
