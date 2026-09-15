"""Shared HTTP connection pools for the Event Mesh broker components.

One sync ``httpx.Client`` (token fetch + management API + consume/ack) and one
async ``httpx.AsyncClient`` (the publish flusher, created lazily by
``start_async``). A single ``EventMeshHttpClients`` instance is handed to every
component (token provider, admin, consumer, publisher) so they reuse the same
pools — and so swapping ``sync`` for a mock in a test is visible to all of them
at once.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass
class EventMeshHttpClients:
    sync: httpx.Client
    async_: httpx.AsyncClient | None = None
