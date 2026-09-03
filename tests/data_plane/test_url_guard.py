"""
Regression tests for ADR 0017 (outbound target egress guard, CWE-918).

target_url is arbitrary client input and the Worker is what dereferences
it, so a gap here turns the data plane into an SSRF proxy for the
cluster's internal network and the cloud metadata service.

The dev bypass RELAY_ALLOW_PRIVATE_TARGETS is deliberately powerful, so
it is pinned too: default-off, and only honoured when explicitly set.
"""
import asyncio
import socket

import pytest

from relay.infrastructure.url_guard import (
    MAX_TARGET_URL_LENGTH,
    UnsafeTargetURL,
    assert_deliverable,
    check_url,
    private_targets_allowed,
)


@pytest.mark.parametrize(
    "url,reason",
    [
        ("http://169.254.169.254/latest/meta-data/", "AWS/Azure metadata"),
        ("http://metadata.google.internal/computeMetadata/", "GCP metadata by name"),
        ("http://100.100.100.200/latest/", "Alibaba metadata"),
        ("http://127.0.0.1:8000/webhook", "loopback literal"),
        ("http://localhost:8000/webhook", "loopback by name"),
        ("http://[::1]:8000/webhook", "IPv6 loopback"),
        ("http://10.0.0.5/internal", "RFC1918 10/8"),
        ("http://192.168.1.10/admin", "RFC1918 192.168/16"),
        ("http://172.16.4.4/admin", "RFC1918 172.16/12"),
        ("http://0.0.0.0/", "unspecified"),
        ("http://[fe80::1]/", "IPv6 link-local"),
        ("http://224.0.0.1/", "multicast"),
        ("file:///etc/passwd", "non-http scheme"),
        ("gopher://example.com/", "non-http scheme"),
        ("redis://cache:6379/", "non-http scheme"),
        ("https://user:pass@example.com/hook", "embedded credentials"),
        ("https:///nohost", "missing host"),
        ("not-a-url", "missing scheme and host"),
    ],
)
def test_unsafe_targets_are_rejected(url, reason):
    with pytest.raises(UnsafeTargetURL):
        check_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.example.com/webhook",
        "http://example.com:8080/path?query=1",
        "https://8.8.8.8/hook",
    ],
)
def test_public_targets_are_allowed(url):
    assert check_url(url)


@pytest.mark.parametrize("ip", ["100.64.0.1", "100.100.100.200", "100.127.255.254"])
def test_rfc6598_shared_address_space_is_rejected(ip):
    """
    CPython's ipaddress does not flag 100.64.0.0/10 through any is_*
    property, so BLOCKED_NETWORKS has to cover it explicitly. Without
    that, Alibaba Cloud's metadata endpoint (100.100.100.200) was a
    reachable target.
    """
    with pytest.raises(UnsafeTargetURL):
        check_url(f"http://{ip}/latest/meta-data/")


@pytest.mark.parametrize(
    "ip",
    [
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "64:ff9b::a9fe:a9fe",
        "2002:7f00:1::",
    ],
)
def test_encoded_forms_of_internal_addresses_are_rejected(ip):
    """IPv4-mapped, NAT64 and 6to4 wrappers must not launder a bad address."""
    with pytest.raises(UnsafeTargetURL):
        check_url(f"http://[{ip}]/webhook")


def test_overlong_url_is_rejected():
    url = "https://example.com/" + "a" * MAX_TARGET_URL_LENGTH

    with pytest.raises(UnsafeTargetURL):
        check_url(url)


def test_trailing_dot_does_not_bypass_the_hostname_blocklist():
    """'localhost.' is a valid FQDN for the same host."""
    with pytest.raises(UnsafeTargetURL):
        check_url("http://localhost./webhook")


def test_uppercase_host_does_not_bypass_the_blocklist():
    with pytest.raises(UnsafeTargetURL):
        check_url("http://LOCALHOST/webhook")


# --------------------------------------------------------------------------
# The dev bypass
# --------------------------------------------------------------------------


def test_bypass_is_off_by_default():
    """conftest clears the var; the guard must not fail open."""
    assert private_targets_allowed() is False


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes"])
def test_bypass_allows_loopback_when_explicitly_enabled(value, monkeypatch):
    """week34_smoke.py depends on this to reach the local Simulator."""
    monkeypatch.setenv("RELAY_ALLOW_PRIVATE_TARGETS", value)

    assert check_url("http://127.0.0.1:9099/webhook") == "127.0.0.1"


@pytest.mark.parametrize("value", ["false", "0", "no", "", "maybe"])
def test_bypass_stays_off_for_non_truthy_values(value, monkeypatch):
    monkeypatch.setenv("RELAY_ALLOW_PRIVATE_TARGETS", value)

    with pytest.raises(UnsafeTargetURL):
        check_url("http://127.0.0.1:9099/webhook")


def test_bypass_still_rejects_a_non_http_scheme(monkeypatch):
    """The bypass relaxes address checks only, never the scheme check."""
    monkeypatch.setenv("RELAY_ALLOW_PRIVATE_TARGETS", "true")

    with pytest.raises(UnsafeTargetURL):
        check_url("file:///etc/passwd")


# --------------------------------------------------------------------------
# DNS-time checks (rebinding)
# --------------------------------------------------------------------------


def stub_dns(monkeypatch, addresses: list[str], *, family=socket.AF_INET):
    """Point the running loop's getaddrinfo at fixed addresses."""
    loop = asyncio.get_running_loop()

    async def fake_getaddrinfo(host, port, **kwargs):
        return [
            (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (addr, 0))
            for addr in addresses
        ]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)


async def test_public_name_resolving_to_metadata_is_blocked(monkeypatch):
    """
    DNS rebinding: the name passes the static check, so only the
    resolve-time check can catch it. This is why assert_deliverable runs
    before every POST and not just at registration.
    """
    stub_dns(monkeypatch, ["169.254.169.254"])

    with pytest.raises(UnsafeTargetURL):
        await assert_deliverable("https://totally-fine.example.com/webhook")


async def test_public_name_resolving_to_rfc1918_is_blocked(monkeypatch):
    stub_dns(monkeypatch, ["10.1.2.3"])

    with pytest.raises(UnsafeTargetURL):
        await assert_deliverable("https://internal.example.com/webhook")


async def test_any_bad_address_in_a_multi_record_answer_blocks(monkeypatch):
    """One public A record must not launder a private one."""
    stub_dns(monkeypatch, ["93.184.216.34", "127.0.0.1"])

    with pytest.raises(UnsafeTargetURL):
        await assert_deliverable("https://mixed.example.com/webhook")


async def test_fully_public_answer_is_allowed(monkeypatch):
    stub_dns(monkeypatch, ["93.184.216.34", "8.8.4.4"])

    await assert_deliverable("https://good.example.com/webhook")


async def test_unresolvable_host_is_rejected(monkeypatch):
    loop = asyncio.get_running_loop()

    async def boom(*args, **kwargs):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(loop, "getaddrinfo", boom)

    with pytest.raises(UnsafeTargetURL):
        await assert_deliverable("https://nx.example.invalid/webhook")


async def test_ipv6_scope_suffix_is_stripped_before_parsing(monkeypatch):
    """getaddrinfo returns link-local v6 as 'fe80::1%eth0'."""
    stub_dns(monkeypatch, ["fe80::1%eth0"], family=socket.AF_INET6)

    with pytest.raises(UnsafeTargetURL):
        await assert_deliverable("https://v6.example.com/webhook")


async def test_bypass_skips_dns_checks_entirely(monkeypatch):
    monkeypatch.setenv("RELAY_ALLOW_PRIVATE_TARGETS", "true")

    await assert_deliverable("http://127.0.0.1:9099/webhook")
