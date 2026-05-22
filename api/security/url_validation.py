"""Outbound URL validation — SSRF guard for any user-configured webhook target.

Issue #127: an admin (or admin-compromised attacker) could point `webhook_url`
at internal/private addresses — critically `http://169.254.169.254/` (AWS EC2
instance metadata endpoint, same vector as Capital One 2019). This validator
rejects schemes other than http(s), IPv4/IPv6 literals in private/loopback/
link-local/multicast/unspecified/reserved ranges, and hostnames whose DNS
A/AAAA records resolve to any address in those ranges.

Design choices:
  - stdlib only (urllib.parse + ipaddress + socket). No new dependencies.
  - DNS resolution at validation time. Multi-A-record defense: if ANY resolved
    address is unsafe, reject the URL — do not "pick the good one".
  - IPv4-mapped IPv6 (e.g. `::ffff:127.0.0.1`) is unwrapped FIRST so the error
    message and Python-version behavior stay consistent (3.10's IPv6
    is_loopback doesn't delegate to ipv4_mapped; 3.12's does).
  - Returns the URL UNMODIFIED on success (no normalization of ports/paths).

Opt-in permissive mode (`allow_private=True`): for trusted internal deployments
(e.g. n8n on localhost, k8s service inside the same cluster). Permits loopback
and RFC1918 private ranges, but **STILL blocks link-local** — the
169.254.169.254 cloud-metadata endpoints that motivated this guard are never
reachable, regardless of the flag. Also still blocks multicast / unspecified
/ reserved. Operators wire this via `cfg.security.webhook_allow_private_ips`.

TOCTOU caveat: an attacker controlling DNS could rebind between validation
and the actual outbound request. Mitigation in this PR: re-call the validator
at push time (defense in depth). Stronger mitigation (pin IP + Host header) is
out of scope — open a follow-up if the threat model demands it.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Optional, Union
from urllib.parse import urlparse


_ALLOWED_SCHEMES = ("http", "https")

_IpAddr = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]


def _check_ip_safe(ip: _IpAddr, *, allow_private: bool = False) -> Optional[str]:
    """Return None if the address is safe to dial, else a human-readable reason.

    Categories that are ALWAYS rejected (load-bearing — `is_link_local`
    covers `169.254.169.254` / Azure / Alibaba IMDS endpoints):
      - link-local, unspecified, multicast, reserved
      - non-http(s) schemes (checked separately in `validate_outbound_url`)
    Categories conditionally rejected (skipped when `allow_private=True`):
      - loopback (127.0.0.0/8, ::1)
      - private (10/8, 172.16/12, 192.168/16, fc00::/7)

    Check order: IPv4-mapped IPv6 is unwrapped FIRST so the message stays
    consistent across Python versions (3.10's `is_loopback` doesn't delegate
    to `ipv4_mapped`; 3.12's does). After that, link-local is checked before
    any other surface so the operator sees the IMDS-specific reason for
    169.254.169.254.
    """
    # IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1): unwrap and re-check so it
    # can't smuggle past the IPv4 surface and so the operator-facing reason
    # is identical across Python versions.
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped
        if mapped is not None:
            inner = _check_ip_safe(mapped, allow_private=allow_private)
            if inner is not None:
                return f"IPv4-mapped IPv6 — {inner}"
            # Mapped IPv4 is safe — continue checking IPv6 surface attributes.

    # Always blocked (load-bearing for IMDS).
    if ip.is_link_local:
        return f"IP link-local ({ip}); incluye AWS metadata 169.254.169.254"
    if ip.is_unspecified:
        return f"IP unspecified ({ip})"
    if ip.is_multicast:
        return f"IP multicast ({ip})"

    # Conditionally blocked. `is_reserved` lives here, NOT above: stdlib
    # classifies `::1` as both `is_loopback` AND `is_reserved` (because the
    # `::/8` block is reserved by IETF), so leaving `is_reserved` in
    # always-blocked would have the opt-in flag refuse to permit IPv6
    # localhost — inconsistent with permitting IPv4 127.0.0.1.
    if not allow_private:
        if ip.is_loopback:
            return f"IP loopback ({ip})"
        if ip.is_private:
            return f"IP privada ({ip})"
        if ip.is_reserved:
            return f"IP reserved ({ip})"
    return None


def validate_outbound_url(url: str, *, allow_private: bool = False) -> str:
    """Validate that `url` is safe for outbound HTTP requests.

    Returns the URL unchanged if safe. Raises `ValueError` with a human-readable
    reason otherwise. The caller (Pydantic validator, request-time check, etc.)
    re-raises so FastAPI surfaces the reason as a 422 detail.

    With `allow_private=True` (operator-set `cfg.security.webhook_allow_private_ips`),
    loopback and RFC1918 private addresses are permitted. Link-local
    (169.254.169.254 IMDS), multicast, unspecified, and reserved ranges are
    STILL rejected — opt-in mode loosens local-network trust, NOT cloud-metadata
    protection.
    """
    if not isinstance(url, str):
        raise ValueError("URL no es string")
    url = url.strip()
    if not url:
        raise ValueError("URL vacía")

    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"scheme '{parsed.scheme}' no permitido (solo http/https)"
        )

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL sin hostname")

    # 1. Literal IP in the hostname → check directly without DNS.
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        reason = _check_ip_safe(literal_ip, allow_private=allow_private)
        if reason is not None:
            raise ValueError(f"hostname IP literal rechazada: {reason}")
        return url

    # 2. Hostname → DNS lookup. Any failure ≡ unsafe to dial.
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, socket.herror, OSError) as e:
        raise ValueError(f"hostname '{hostname}' no resuelve: {e}") from e

    if not infos:
        raise ValueError(f"hostname '{hostname}' no devolvió direcciones")

    # 3. Multi-A-record defense: if any single resolved IP is unsafe, reject.
    for _family, _socktype, _proto, _canonname, sockaddr in infos:
        ip_str = sockaddr[0]
        # IPv6 sockaddr can include a scope id like 'fe80::1%eth0'.
        if "%" in ip_str:
            ip_str = ip_str.split("%", 1)[0]
        try:
            resolved = ipaddress.ip_address(ip_str)
        except ValueError as e:
            raise ValueError(
                f"DNS devolvió IP no parseable '{ip_str}': {e}"
            ) from e
        reason = _check_ip_safe(resolved, allow_private=allow_private)
        if reason is not None:
            raise ValueError(
                f"hostname '{hostname}' resuelve a IP rechazada: {reason}"
            )

    return url
