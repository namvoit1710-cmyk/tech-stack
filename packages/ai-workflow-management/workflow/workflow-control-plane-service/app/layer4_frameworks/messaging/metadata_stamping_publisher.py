"""Decorator that stamps caller-supplied run metadata onto every domain event.

Wraps any ``IEventPublisher`` and, before delegating, looks up the run's
free-form ``metadata`` (captured at ``POST /runs``) and sets it on the event's
``metadata`` field. Because every event serializes via ``dataclasses.asdict``,
the field then rides into the realtime envelope and the centralized
``WORKFLOW_EVENT_TOPIC`` envelope without any per-sink change.

It is the OUTERMOST decorator in the publish chain so the stamped event is the
one every downstream publisher (event log, realtime broadcast, centralized
enrich, original topic) serializes:

```
MetadataStampingPublisher  (this file)
  → EventLogAppending → RealtimeBroadcasting → Enriching → Kafka/EventMesh/Console
```

Metadata is looked up by ``root_run_id`` first so child / sub-workflow events
inherit the root run's metadata. The lookup is cached per run id — run metadata
is immutable after start, so one repo read per run suffices. All work is
best-effort: any failure is swallowed so event publishing is never blocked.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from app.layer2_application.interfaces.event_publisher_interface import IEventPublisher
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer4_frameworks.caching.bounded_ttl_cache import (
    MISS,
    BoundedTTLCache,
    CacheStats,
    env_int,
)

logger = logging.getLogger(__name__)

# M3 (SA-2260) — bounds for the run-metadata cache on the publish hot path.
# The cap is what bounds memory: the key space is every run this pod ever
# publishes for, so a TTL alone would still hold 10k entries if 10k runs
# arrive inside one window. The TTL is only a staleness backstop; run metadata
# is immutable after start, which is why it can be generous.
_METADATA_CACHE_MAX = env_int("METADATA_STAMP_CACHE_MAX", 2048)
_METADATA_CACHE_TTL_S = env_int("METADATA_STAMP_CACHE_TTL_S", 900, minimum=0)
# A miss is normally transient — the run row is not visible to this replica
# yet. Worth caching so a burst of events for one run does not hammer the
# repo, but never for long, and never forever (which is what it did before).
_METADATA_MISS_TTL_S = env_int("METADATA_STAMP_MISS_TTL_S", 10, minimum=0)


class MetadataStampingPublisher(IEventPublisher):
    """Stamp run metadata onto each event, then delegate to the inner publisher."""

    def __init__(
        self,
        inner: IEventPublisher,
        run_repo: IRunRepository | None = None,
        cache: BoundedTTLCache | None = None,
    ) -> None:
        self._inner = inner
        self._run_repo = run_repo
        # run_id → metadata dict. Run metadata never changes after start, so
        # caching it is safe — but this ran as a plain dict on the publish hot
        # path with no cap, no TTL and no eviction, and it negative-cached a
        # not-found run permanently, so misses accumulated too (M3 — SA-2260).
        # Now: LRU-capped for the memory guarantee, TTL'd for staleness, with
        # a separate short life for negatives.
        self._cache = cache if cache is not None else BoundedTTLCache(
            maxsize=_METADATA_CACHE_MAX,
            ttl_seconds=_METADATA_CACHE_TTL_S,
            name="metadata_stamping",
        )

    def publish(self, topic: str, event: Any) -> None:
        try:
            self._stamp(event)
        except Exception as exc:  # noqa: BLE001 — never block publishing
            logger.debug("Metadata stamping skipped: %s", exc)
        self._inner.publish(topic, event)

    def _stamp(self, event: Any) -> None:
        if not self._run_repo:
            return
        if not (dataclasses.is_dataclass(event) and not isinstance(event, type)):
            return
        # Only stamp events that declare a ``metadata`` field and haven't been
        # stamped already (e.g. the engine pre-populates NodeDispatched).
        if not hasattr(event, "metadata") or getattr(event, "metadata"):
            return
        run_key = getattr(event, "root_run_id", "") or getattr(event, "run_id", "")
        if not run_key:
            return
        metadata = self._lookup(run_key)
        if metadata:
            event.metadata = metadata

    def _lookup(self, run_key: str) -> dict:
        # MISS is a sentinel, not None/falsy: ``{}`` is a legitimate cached
        # value (a run that genuinely has no metadata) and must count as a hit.
        cached = self._cache.get(run_key)
        if cached is not MISS:
            return cached
        metadata: dict = {}
        run = self._run_repo.get_by_id(run_key)
        if run is not None:
            metadata = getattr(run, "metadata", None) or {}
            self._cache.put(run_key, metadata)
        else:
            self._cache.put(run_key, metadata, ttl_seconds=_METADATA_MISS_TTL_S)
        return metadata

    def cache_stats(self) -> CacheStats:
        """Bucket snapshot for the SA-2256 memory-budget export."""
        return self._cache.stats()
