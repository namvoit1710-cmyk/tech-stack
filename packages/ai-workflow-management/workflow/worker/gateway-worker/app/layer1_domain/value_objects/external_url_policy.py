"""External URL allowlist + SSRF defenses for call_external.

Policy enforcement order in `check(url)`:
  1. URL is absolute and uses an allowed scheme (default: https only).
  2. Host matches the allowlist (exact or one-level wildcard `*.partner.com`).
  3. Resolved IP is not in private / loopback / link-local / multicast space.

`resolve_host()` returns the IP the upstream connect should use. Callers
should connect to the IP (with the original Host header) rather than
re-resolving — defeats a DNS-rebind attack that would otherwise let an
attacker pass step 3 then point at 127.0.0.1.
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from collections.abc import Callable
from urllib.parse import urlparse


class HostNotAllowed(Exception):
    pass


class SchemeNotAllowed(Exception):
    pass


class PrivateIpBlocked(Exception):
    pass


@dataclass(frozen=True)
class ExternalUrlPolicy:
    allowed_hosts: tuple[str, ...]
    allowed_schemes: tuple[str, ...] = ("https",)
    # Injectable for tests; defaults to socket.gethostbyname.
    resolver: Callable[[str], str] | None = None

    def check(self, url: str) -> str:
        """Validate the URL end-to-end and return the host part. Raises on rejection."""
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        if scheme not in self.allowed_schemes:
            raise SchemeNotAllowed(
                f"scheme {scheme!r} not in allowed schemes {list(self.allowed_schemes)}"
            )
        host = (parsed.hostname or "").lower()
        if not host:
            raise HostNotAllowed("URL has no host component")
        if not _host_matches_any(host, self.allowed_hosts):
            raise HostNotAllowed(
                f"host {host!r} is not in the allowlist"
            )
        resolved_ip = self.resolve_host(host)
        if _is_private_ip(resolved_ip):
            raise PrivateIpBlocked(
                f"host {host!r} resolved to private/loopback IP {resolved_ip}"
            )
        return host

    def resolve_host(self, host: str) -> str:
        resolver = self.resolver or socket.gethostbyname
        try:
            return resolver(host)
        except OSError as exc:
            raise HostNotAllowed(f"DNS resolution failed for {host}: {exc}") from exc


def _host_matches_any(host: str, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        if _host_matches(host, pattern.lower().strip()):
            return True
    return False


def _host_matches(host: str, pattern: str) -> bool:
    if not pattern:
        return False
    if not pattern.startswith("*."):
        return host == pattern
    # One-level wildcard: `*.partner.com` matches `api.partner.com` but
    # NOT `a.b.partner.com` or `partner.com` itself.
    suffix = pattern[2:]
    if not host.endswith("." + suffix):
        return False
    label = host[: -len(suffix) - 1]
    return bool(label) and "." not in label


def _is_private_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        # If it's not a parsable IP we don't know what we're connecting to —
        # err on the side of refusing.
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )
