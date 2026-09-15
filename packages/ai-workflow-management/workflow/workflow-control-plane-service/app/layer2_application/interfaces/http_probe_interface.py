from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class HttpProbeResult:
    reachable: bool
    status_code: int = 0
    latency_ms: float = 0.0
    error: str = ""


class IHttpProbe(Protocol):
    """A one-shot HTTP GET against a URL with optional headers.

    Used by the gateway service registry `test` endpoint to verify that a
    registered service is reachable with its configured credential. Kept
    deliberately narrow so it can be stubbed in tests without pulling in
    httpx.
    """

    async def __call__(
        self,
        url: str,
        headers: dict[str, str],
        timeout_s: float,
    ) -> HttpProbeResult: ...
