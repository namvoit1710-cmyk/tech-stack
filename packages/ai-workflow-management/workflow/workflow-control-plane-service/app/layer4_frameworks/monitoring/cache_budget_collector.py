"""Expose the 256MB memory budget on the existing /metrics scrape (SA-2256).

A prometheus_client *custom collector*: ``collect()`` is invoked by
``generate_latest`` at scrape time, so every number is read from the live cache
at the moment it is asked for. Nothing is pushed, nothing runs on a timer, and
a bucket costs nothing when nobody scrapes.

Emitted series::

    wcp_cache_entries{bucket="metadata_stamping"}          12
    wcp_cache_maxsize{bucket="metadata_stamping"}        2048
    wcp_cache_hits{bucket="metadata_stamping"}          8134
    ...
    wcp_cache_cache{bucket="projector_view_store"}         41
    wcp_cache_materialize_entries{bucket="run_scoped_materialize"}  3

One metric family per FIELD, labelled by bucket — not one family per bucket —
so a new cache joining the registry needs no change here and no dashboard
rewiring.

No new egress: this rides the ``/metrics`` endpoint the CP already serves.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

from prometheus_client.core import GaugeMetricFamily

from app.layer2_application.interfaces.cache_budget_registry import (
    CacheBudgetRegistry,
    default_cache_budget_registry,
)

logger = logging.getLogger(__name__)

_METRIC_PREFIX = "wcp_cache_"
# Prometheus metric names allow [a-zA-Z_:][a-zA-Z0-9_:]* — bucket snapshots are
# authored in this repo, but sanitising keeps a future typo from poisoning the
# whole scrape with an unparseable exposition line.
_SAFE = re.compile(r"[^a-zA-Z0-9_]")


def _metric_name(field: str) -> str:
    cleaned = _SAFE.sub("_", field).strip("_") or "value"
    return f"{_METRIC_PREFIX}{cleaned}"


class CacheBudgetCollector:
    """Turns :class:`CacheBudgetRegistry` snapshots into Prometheus gauges."""

    def __init__(self, registry: CacheBudgetRegistry | None = None) -> None:
        self._registry = registry or default_cache_budget_registry()

    def collect(self) -> Iterable[GaugeMetricFamily]:
        try:
            snapshot = self._registry.snapshot()
        except Exception:
            # A scrape must never take the pod down, and a failed scrape must
            # never look like "all caches are empty".
            logger.warning("cache budget snapshot failed", exc_info=True)
            return []

        families: dict[str, GaugeMetricFamily] = {}
        for bucket, values in sorted(snapshot.items()):
            for field, value in sorted(values.items()):
                name = _metric_name(field)
                family = families.get(name)
                if family is None:
                    family = GaugeMetricFamily(
                        name,
                        f"SA-2254 memory budget: {field} per cache bucket",
                        labels=["bucket"],
                    )
                    families[name] = family
                family.add_metric([bucket], float(value))
        return list(families.values())


def install_cache_budget_collector(monitor, registry: CacheBudgetRegistry | None = None):
    """Attach the collector to ``monitor``'s Prometheus registry.

    Returns the collector on success and ``None`` when the monitor has no
    registry (the stub monitor used in tests), so bootstrap can stay a
    one-liner and metrics remain optional.
    """
    prom_registry = getattr(monitor, "registry", None)
    if prom_registry is None:
        return None
    collector = CacheBudgetCollector(registry)
    try:
        prom_registry.register(collector)
    except ValueError:
        # Already installed (double bootstrap in a test process) — harmless.
        logger.debug("cache budget collector already registered")
        return None
    return collector
