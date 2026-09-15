"""In-memory ``IMonitor`` implementation.

Stores every observation in a list so tests (and ad-hoc diagnostics) can
inspect what the engine / resolver recorded without wiring a real
Prometheus client. Not suitable for production — observations are kept
forever in memory and never scraped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.monitor_interface import IMonitor


@dataclass(frozen=True)
class HistogramObservation:
    metric: str
    value: float
    tags: dict[str, Any]


@dataclass(frozen=True)
class CounterObservation:
    metric: str
    tags: dict[str, Any]
    amount: int = 1


@dataclass(frozen=True)
class GaugeObservation:
    metric: str
    value: float


@dataclass
class InMemoryMonitor(IMonitor):
    """Captures every metric call for later inspection."""

    histograms: list[HistogramObservation] = field(default_factory=list)
    counters: list[CounterObservation] = field(default_factory=list)
    gauges: list[GaugeObservation] = field(default_factory=list)

    def track(self, metric: str, value: float) -> None:
        self.gauges.append(GaugeObservation(metric=metric, value=value))

    def increment(
        self, metric: str, tags: dict | None = None, amount: int = 1,
    ) -> None:
        self.counters.append(
            CounterObservation(
                metric=metric, tags=dict(tags or {}), amount=int(amount),
            ),
        )

    def histogram(
        self, metric: str, value: float, tags: dict | None = None,
    ) -> None:
        self.histograms.append(
            HistogramObservation(
                metric=metric,
                value=float(value),
                tags=dict(tags or {}),
            ),
        )

    # ---- Query helpers (test ergonomics) -----------------------------

    def histogram_observations(self, metric: str) -> list[HistogramObservation]:
        """Return every histogram observation recorded for *metric*."""
        return [obs for obs in self.histograms if obs.metric == metric]

    def histogram_count(self, metric: str, tags: dict | None = None) -> int:
        """Return how many observations match *metric* (and optional *tags*)."""
        target_tags = dict(tags or {})
        return sum(
            1 for obs in self.histograms
            if obs.metric == metric
            and all(obs.tags.get(k) == v for k, v in target_tags.items())
        )

    def clear(self) -> None:
        self.histograms.clear()
        self.counters.clear()
        self.gauges.clear()
