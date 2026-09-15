"""Prometheus-backed IMonitor implementation.

Lazy-creates Counter/Histogram/Gauge collectors on first observation.
The set of label keys is fixed at collector creation time — the first
call to ``increment("foo", tags={"a": 1})`` defines ``foo`` as a Counter
with label ``a``. Subsequent calls with a different label set log a
warning and are dropped rather than corrupting the metric.

Use cases and controllers already emit via :class:`IMonitor`; nothing in
the call sites changes when this swap happens.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
)

from app.layer2_application.interfaces.monitor_interface import IMonitor


logger = logging.getLogger(__name__)


# Histogram buckets tuned for request-latency seconds: 1ms → 5s covers
# typical CP endpoint behavior. Adjust per-metric if needed later.
_DEFAULT_BUCKETS = (
    0.001, 0.005, 0.01, 0.025, 0.05,
    0.1, 0.25, 0.5, 1.0, 2.5, 5.0,
)


class PrometheusMonitor(IMonitor):
    """Real Prometheus-backed monitor with lazy metric creation."""

    def __init__(self, registry: Optional[CollectorRegistry] = None) -> None:
        # A private registry makes this safely instantiable in tests without
        # polluting the global default registry. Production bootstrap passes
        # a dedicated registry that /metrics scrapes.
        self._registry = registry or CollectorRegistry()
        self._counters: dict[str, Counter] = {}
        self._histograms: dict[str, Histogram] = {}
        self._gauges: dict[str, Gauge] = {}
        self._labels_for: dict[str, tuple[str, ...]] = {}
        self._lock = threading.Lock()

    @property
    def registry(self) -> CollectorRegistry:
        return self._registry

    # -- IMonitor --------------------------------------------------------

    def track(self, metric: str, value: float) -> None:
        gauge = self._get_gauge(metric)
        if gauge is not None:
            gauge.set(value)

    def increment(
        self, metric: str, tags: Optional[dict] = None, amount: int = 1,
    ) -> None:
        counter = self._get_counter(metric, tags)
        if counter is None:
            return
        if amount <= 0:
            return  # a Prometheus counter cannot go backwards
        if tags:
            counter.labels(**{k: str(v) for k, v in tags.items()}).inc(amount)
        else:
            counter.inc(amount)

    def histogram(
        self,
        metric: str,
        value: float,
        tags: Optional[dict] = None,
    ) -> None:
        hist = self._get_histogram(metric, tags)
        if hist is None:
            return
        if tags:
            hist.labels(**{k: str(v) for k, v in tags.items()}).observe(value)
        else:
            hist.observe(value)

    # -- lazy creation ---------------------------------------------------

    def _get_counter(self, name: str, tags: Optional[dict]) -> Optional[Counter]:
        with self._lock:
            if name in self._counters:
                if not self._label_set_matches(name, tags):
                    return None
                return self._counters[name]
            labels = self._capture_labels(name, tags)
            try:
                self._counters[name] = Counter(
                    name,
                    f"Counter: {name}",
                    labelnames=labels,
                    registry=self._registry,
                )
                return self._counters[name]
            except ValueError as exc:
                logger.warning("Prometheus counter creation failed (%s): %s", name, exc)
                return None

    def _get_histogram(self, name: str, tags: Optional[dict]) -> Optional[Histogram]:
        with self._lock:
            if name in self._histograms:
                if not self._label_set_matches(name, tags):
                    return None
                return self._histograms[name]
            labels = self._capture_labels(name, tags)
            try:
                self._histograms[name] = Histogram(
                    name,
                    f"Histogram: {name}",
                    labelnames=labels,
                    registry=self._registry,
                    buckets=_DEFAULT_BUCKETS,
                )
                return self._histograms[name]
            except ValueError as exc:
                logger.warning("Prometheus histogram creation failed (%s): %s", name, exc)
                return None

    def _get_gauge(self, name: str) -> Optional[Gauge]:
        with self._lock:
            if name in self._gauges:
                return self._gauges[name]
            try:
                self._gauges[name] = Gauge(
                    name,
                    f"Gauge: {name}",
                    registry=self._registry,
                )
                return self._gauges[name]
            except ValueError as exc:
                logger.warning("Prometheus gauge creation failed (%s): %s", name, exc)
                return None

    def _capture_labels(self, name: str, tags: Optional[dict]) -> tuple[str, ...]:
        keys: tuple[str, ...] = tuple(sorted(tags.keys())) if tags else ()
        self._labels_for[name] = keys
        return keys

    def _label_set_matches(self, name: str, tags: Optional[dict]) -> bool:
        declared = self._labels_for.get(name, ())
        observed = tuple(sorted(tags.keys())) if tags else ()
        if declared != observed:
            logger.warning(
                "Prometheus metric %r label-set mismatch: declared=%s observed=%s — "
                "dropping sample",
                name, declared, observed,
            )
            return False
        return True
