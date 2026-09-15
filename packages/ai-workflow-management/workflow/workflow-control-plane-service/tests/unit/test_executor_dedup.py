"""Phase 3 Sub 3.5 — ExecutorDedup."""

from __future__ import annotations

import pytest

from app.layer4_frameworks.executor.executor_dedup import ExecutorDedup


class TestBasic:
    def test_first_call_is_not_executed(self):
        d = ExecutorDedup()
        assert d.already_executed("r", "n", 0) is False

    def test_mark_then_check_is_executed(self):
        d = ExecutorDedup()
        d.mark_executed("r", "n", 0)
        assert d.already_executed("r", "n", 0) is True

    def test_different_attempt_is_not_executed(self):
        d = ExecutorDedup()
        d.mark_executed("r", "n", 0)
        assert d.already_executed("r", "n", 1) is False

    def test_different_node_is_not_executed(self):
        d = ExecutorDedup()
        d.mark_executed("r", "n1", 0)
        assert d.already_executed("r", "n2", 0) is False

    def test_different_run_is_not_executed(self):
        d = ExecutorDedup()
        d.mark_executed("r1", "n", 0)
        assert d.already_executed("r2", "n", 0) is False

    def test_double_mark_is_idempotent(self):
        d = ExecutorDedup()
        d.mark_executed("r", "n", 0)
        d.mark_executed("r", "n", 0)
        assert len(d) == 1


class TestEviction:
    def test_evicts_oldest_when_capped(self):
        d = ExecutorDedup(capacity=3)
        d.mark_executed("r", "a", 0)
        d.mark_executed("r", "b", 0)
        d.mark_executed("r", "c", 0)
        d.mark_executed("r", "d", 0)  # forces eviction of "a"
        assert d.already_executed("r", "a", 0) is False
        assert d.already_executed("r", "b", 0) is True
        assert d.already_executed("r", "d", 0) is True

    def test_already_executed_promotes_to_mru(self):
        d = ExecutorDedup(capacity=3)
        d.mark_executed("r", "a", 0)
        d.mark_executed("r", "b", 0)
        d.mark_executed("r", "c", 0)
        # Touch "a" — now "b" is the LRU.
        assert d.already_executed("r", "a", 0) is True
        d.mark_executed("r", "d", 0)  # evicts "b"
        assert d.already_executed("r", "a", 0) is True
        assert d.already_executed("r", "b", 0) is False

    def test_capacity_zero_rejected(self):
        with pytest.raises(ValueError):
            ExecutorDedup(capacity=0)
