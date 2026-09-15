"""Unit spec for LoadBalancer (layer2 service — round-robin worker selection).

Grounds every assertion in app/layer2_application/services/load_balancer.py:
    if not workers: return None
    selected = workers[cls._counter % len(workers)]
    cls._counter += 1
    return selected

The round-robin cursor is a CLASS-level attribute (``LoadBalancer._counter``)
shared across all calls, so each test resets it first for determinism — the
same discipline the source's shared cursor demands of any caller.

Uses lightweight WorkerRegistration domain entities (no mocks — pure selection).
"""

from __future__ import annotations

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer2_application.services.load_balancer import LoadBalancer
from app.layer1_domain.entities.worker_registration import WorkerRegistration


@pytest.fixture(autouse=True)
def _reset_counter():
    """The round-robin cursor is class-level state; zero it before each test."""
    LoadBalancer._counter = 0
    yield
    LoadBalancer._counter = 0


def _workers(*names: str) -> list[WorkerRegistration]:
    return [WorkerRegistration(id=n, name=n) for n in names]


# --- Happy path: selects from the pool ---

def test_select_worker_returns_a_worker_from_the_list():
    workers = _workers("a", "b", "c")
    selected = LoadBalancer.select_worker(workers)
    assert selected in workers
    assert selected.id == "a"  # counter starts at 0 → index 0


# --- Round-robin rotation (the reason this class exists) ---

def test_select_worker_rotates_round_robin():
    workers = _workers("a", "b", "c")
    picks = [LoadBalancer.select_worker(workers).id for _ in range(3)]
    assert picks == ["a", "b", "c"]


def test_select_worker_wraps_after_exhausting_pool():
    """Boundary: the counter modulo len wraps back to the first worker."""
    workers = _workers("a", "b")
    picks = [LoadBalancer.select_worker(workers).id for _ in range(5)]
    assert picks == ["a", "b", "a", "b", "a"]


# --- Boundary: single-worker pool always returns that worker ---

def test_single_worker_pool_always_returns_it():
    workers = _workers("solo")
    for _ in range(4):
        assert LoadBalancer.select_worker(workers).id == "solo"


# --- Edge / invalid input: empty list returns None (source guard) ---

def test_empty_list_returns_none():
    assert LoadBalancer.select_worker([]) is None


def test_empty_list_does_not_advance_counter():
    """The early ``return None`` happens before ``_counter += 1`` — the cursor
    must not move when there is nothing to select."""
    LoadBalancer._counter = 0
    LoadBalancer.select_worker([])
    assert LoadBalancer._counter == 0


# --- Counter advances exactly once per successful selection ---

def test_counter_increments_by_one_per_selection():
    workers = _workers("a", "b", "c")
    LoadBalancer._counter = 0
    LoadBalancer.select_worker(workers)
    assert LoadBalancer._counter == 1
    LoadBalancer.select_worker(workers)
    assert LoadBalancer._counter == 2


def test_selection_resumes_from_existing_counter_offset():
    """A non-zero starting cursor selects the modulo-indexed worker (shared
    state across the app is intentional)."""
    workers = _workers("a", "b", "c")
    LoadBalancer._counter = 4  # 4 % 3 == 1 → "b"
    assert LoadBalancer.select_worker(workers).id == "b"
