"""Unit spec for DiscoveryEngine (layer2 service — capability lookup).

Grounds every assertion in app/layer2_application/services/discovery_engine.py:
    @staticmethod
    def find_workers(capability, registry):
        return registry.discover(capability)

The single collaborator is IWorkerRegistryStore. ``discover`` is a SYNC method
(see worker_registry_store_interface.py) → mock it with MagicMock, NOT AsyncMock
(MOCK_KIND_DRIFT would return a coroutine, not the list). The engine is a thin
delegating pass-through, so the cases assert exact delegation + faithful return.
"""

from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer2_application.services.discovery_engine import DiscoveryEngine
from app.layer1_domain.entities.worker_registration import WorkerRegistration


# --- Happy path: delegates to registry.discover and returns its result ---

def test_find_workers_delegates_to_registry_discover():
    matching = [WorkerRegistration(id="w1", name="w1")]
    registry = MagicMock()
    registry.discover.return_value = matching

    result = DiscoveryEngine.find_workers("data:transform", registry)

    assert result == matching
    registry.discover.assert_called_once_with("data:transform")


def test_find_workers_passes_capability_through_verbatim():
    registry = MagicMock()
    registry.discover.return_value = []
    DiscoveryEngine.find_workers("some:very-specific-capability", registry)
    registry.discover.assert_called_once_with("some:very-specific-capability")


def test_find_workers_returns_multiple_matches():
    matches = [
        WorkerRegistration(id="a", name="a"),
        WorkerRegistration(id="b", name="b"),
    ]
    registry = MagicMock()
    registry.discover.return_value = matches
    result = DiscoveryEngine.find_workers("cap", registry)
    assert len(result) == 2
    assert [w.id for w in result] == ["a", "b"]


# --- Edge / boundary: registry finds nothing ---

def test_find_workers_returns_empty_when_no_match():
    registry = MagicMock()
    registry.discover.return_value = []
    result = DiscoveryEngine.find_workers("unknown:capability", registry)
    assert result == []
    registry.discover.assert_called_once_with("unknown:capability")


def test_find_workers_with_empty_capability_still_delegates():
    """Edge: an empty capability string is not filtered by the engine — it is
    the registry's decision. The engine must delegate as-is."""
    registry = MagicMock()
    registry.discover.return_value = []
    DiscoveryEngine.find_workers("", registry)
    registry.discover.assert_called_once_with("")


# --- Failure path: registry raises → engine does not swallow it ---

def test_find_workers_propagates_registry_error():
    """DiscoveryEngine has no try/except — an error from the store must
    propagate to the caller unchanged."""
    registry = MagicMock()
    registry.discover.side_effect = RuntimeError("registry unavailable")
    with pytest.raises(RuntimeError, match="registry unavailable"):
        DiscoveryEngine.find_workers("cap", registry)
