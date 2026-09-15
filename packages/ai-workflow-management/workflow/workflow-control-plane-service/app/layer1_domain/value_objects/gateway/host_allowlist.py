from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.layer1_domain.exceptions import ValidationError

# Hostname or one-level wildcard (``*.example.com``). No scheme, port, or path.
_HOST_RE = re.compile(r"^(\*\.)?([A-Za-z0-9-]+\.)*[A-Za-z0-9-]+$")


@dataclass(frozen=True)
class HostAllowlist:
    """The set of hosts an EXTERNAL app's functions may call.

    Self-validating: each entry must be a bare host or a one-level wildcard
    (``*.partner.com``) — no scheme, port, or path. The gateway-worker's
    ``ExternalUrlPolicy`` enforces the same shape at request time (P4); this VO
    guarantees only well-formed allowlists are ever stored.
    """

    hosts: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.hosts, tuple):
            object.__setattr__(self, "hosts", tuple(self.hosts))
        for host in self.hosts:
            if not host or not host.strip():
                raise ValidationError("allowed_hosts entries must not be empty")
            if not _HOST_RE.match(host):
                raise ValidationError(
                    f"allowed_hosts entry '{host}' is not a bare host or one-level "
                    "wildcard (e.g. 'api.partner.com' or '*.partner.com')"
                )

    def is_empty(self) -> bool:
        return len(self.hosts) == 0

    def matches(self, host: str) -> bool:
        """True if ``host`` is permitted: exact match or one-level wildcard.

        Mirrors the gateway-worker's ``ExternalUrlPolicy`` host check so the CP
        can validate an external function's target host at author time (P4).
        ``*.partner.com`` matches ``api.partner.com`` but not ``partner.com`` or
        ``a.b.partner.com``.
        """
        if not host or not host.strip():
            return False
        host = host.strip().lower()
        for entry in self.hosts:
            e = entry.strip().lower()
            if e.startswith("*."):
                suffix = e[1:]  # ".partner.com"
                if host.endswith(suffix):
                    label = host[: -len(suffix)]
                    if label and "." not in label:
                        return True
            elif host == e:
                return True
        return False

    def to_list(self) -> list[str]:
        return list(self.hosts)

    @classmethod
    def from_list(cls, values: list[str] | None) -> "HostAllowlist":
        return cls(hosts=tuple(values or ()))
