"""Broker topic consumers (non-realtime infrastructure)."""

from app.layer4_frameworks.messaging.consumers.catalog_changed_consumer import (
    CATALOG_CHANGED_TOPIC,
    CatalogChangedConsumer,
)

__all__ = ["CatalogChangedConsumer", "CATALOG_CHANGED_TOPIC"]
