from __future__ import annotations

from enum import Enum


class AppKind(str, Enum):
    """Where a gateway app's functions are proxied to.

    * ``internal`` — a registered upstream addressed by ``base_url`` + injected
      credential (the old ``call_internal`` path).
    * ``external`` — a public endpoint addressed by a full URL, constrained to
      the app's ``allowed_hosts`` (the old ``call_external`` path).
    """

    INTERNAL = "internal"
    EXTERNAL = "external"
