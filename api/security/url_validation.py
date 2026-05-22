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
  - IPv4-mapped IPv6 (e.g. `::ffff:127.0.0.1`) is unwrapped and re-checked
    so it cannot smuggle past per-protocol checks.
  - Returns the URL UNMODIFIED on success (no normalization of ports/paths).

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


def _check_ip_safe(ip: _IpAddr) -> Optional[str]:
    """Return None if the address is safe to dial, else a human-readable reason.

    Check order matters only for the *error message*: every flag below
    independently disqualifies the address. We test the most specific
    sub-categories first so the operator sees the most informative reason
    (e.g. `0.0.0.0` is both `is_unspecified` and `is_private` in Python
    3.10+; we want the operator to see "unspecified" because it's the
    actionable label).
    """
    if ip.is_loopback:
        return f"IP loopback ({ip})"
    if ip.is_link_local:
        return f"IP link-local ({ip}); incluye AWS metadata 169.254.169.254"
    if ip.is_unspecified:
        return f"IP unspecified ({ip})"
    if ip.is_multicast:
        return f"IP multicast ({ip})"
    if ip.is_private:
        return f"IP privada ({ip})"
    if ip.is_reserved:
        return f"IP reserved ({ip})"
    # IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1) — unwrap and re-check so it can't
    # smuggle past the IPv4 checks above.
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped
        if mapped is not None:
            inner = _check_ip_safe(mapped)
            if inner is not None:
                return f"IPv4-mapped IPv6 — {inner}"
    return None


def validate_outbound_url(url: str) -> str:
    """Validate that `url` is safe for outbound HTTP requests.

    Returns the URL unchanged if safe. Raises `ValueError` with a human-readable
    reason otherwise. The caller (Pydantic validator, request-time check, etc.)
    re-raises so FastAPI surfaces the reason as a 422 detail.
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
        reason = _check_ip_safe(literal_ip)
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
        reason = _check_ip_safe(resolved)
        if reason is not None:
            raise ValueError(
                f"hostname '{hostname}' resuelve a IP rechazada: {reason}"
            )

    return url
