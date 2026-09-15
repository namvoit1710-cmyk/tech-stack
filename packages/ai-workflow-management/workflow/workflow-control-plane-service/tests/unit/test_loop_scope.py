"""Unit tests for the Phase 4c.4 loop scope foundation.

Covers:
- ``LoopScope`` frozen-dataclass semantics and JSON round-trip via
  ``to_dict`` / ``from_dict``.
- ``derive_invocation_id`` determinism — same inputs always produce
  the same id — and the critical **nested-loop uniqueness** property:
  different outer iterations MUST produce different inner invocation
  ids, because that's what lets merge nodes inside a nested body get
  fresh quorum buffers per outer iteration.
- ``Task.get_innermost_scope`` prefers the Phase 4c.4 ``scope_stack``
  but falls back to synthesising a legacy scope from
  ``loop_context`` / ``iteration_index`` so the helper works uniformly
  across the migration.
- ``Task.with_pushed_scope`` returns a NEW task (immutable-style)
  rather than mutating the source.
"""

from __future__ import annotations

import uuid

import pytest

from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects.loop_scope import (
    LoopScope,
    derive_invocation_id,
)


# ──────────────────────── LoopScope ────────────────────────────


class TestLoopScope:
    def test_basic_construction(self):
        scope = LoopScope(
            loop_node_id="loop-1", invocation_id="inv-1", iter=0, total=3,
        )
        assert scope.loop_node_id == "loop-1"
        assert scope.invocation_id == "inv-1"
        assert scope.iter == 0
        assert scope.total == 3

    def test_frozen_cannot_mutate(self):
        scope = LoopScope("loop-1", "inv-1", 0, 3)
        with pytest.raises(Exception):
            scope.iter = 5  # type: ignore[misc]

    def test_to_dict_round_trip(self):
        original = LoopScope("loop-1", "inv-abc", iter=2, total=5)
        serialised = original.to_dict()
        assert serialised == {
            "loop_node_id": "loop-1",
            "invocation_id": "inv-abc",
            "iter": 2,
            "total": 5,
        }

        rebuilt = LoopScope.from_dict(serialised)
        assert rebuilt == original

    def test_from_dict_handles_missing_keys_with_defaults(self):
        """Defensive rebuild from a partial dict — never blow up."""
        scope = LoopScope.from_dict({})
        assert scope.loop_node_id == ""
        assert scope.invocation_id == ""
        assert scope.iter == 0
        assert scope.total == 0


# ──────────────────── derive_invocation_id ─────────────────────


class TestDeriveInvocationId:
    def test_same_inputs_produce_same_id(self):
        """Deterministic replay requires stable ids."""
        id1 = derive_invocation_id("run-1", "loop-outer")
        id2 = derive_invocation_id("run-1", "loop-outer")
        assert id1 == id2

    def test_different_run_ids_produce_different_ids(self):
        id1 = derive_invocation_id("run-1", "loop-outer")
        id2 = derive_invocation_id("run-2", "loop-outer")
        assert id1 != id2

    def test_different_loop_node_ids_produce_different_ids(self):
        id1 = derive_invocation_id("run-1", "loop-a")
        id2 = derive_invocation_id("run-1", "loop-b")
        assert id1 != id2

    def test_nested_invocation_differs_per_outer_iteration(self):
        """The critical property — an inner loop inside a nested body
        must get a DIFFERENT invocation id for each outer iteration,
        so merge buffers and state don't leak between outer iterations.
        """
        outer_id = derive_invocation_id("run-1", "loop-outer")
        outer_iter_0 = LoopScope("loop-outer", outer_id, iter=0, total=2)
        outer_iter_1 = LoopScope("loop-outer", outer_id, iter=1, total=2)

        inner_for_outer_0 = derive_invocation_id(
            "run-1", "loop-inner", [outer_iter_0],
        )
        inner_for_outer_1 = derive_invocation_id(
            "run-1", "loop-inner", [outer_iter_1],
        )

        assert inner_for_outer_0 != inner_for_outer_1, (
            "inner loop MUST get a different invocation id for each "
            "outer iteration — otherwise merge quorum buffers would "
            "leak between outer iterations"
        )

    def test_same_outer_iteration_produces_same_inner_id(self):
        """Replay property — inside the same outer iteration, the
        inner loop's invocation id is stable across replays."""
        outer_id = derive_invocation_id("run-1", "loop-outer")
        outer = LoopScope("loop-outer", outer_id, iter=0, total=2)

        first = derive_invocation_id("run-1", "loop-inner", [outer])
        second = derive_invocation_id("run-1", "loop-inner", [outer])
        assert first == second

    def test_returns_valid_uuid5_hex(self):
        result = derive_invocation_id("run-1", "loop-1")
        # Should be a 32-char hex string (uuid5 hex without dashes)
        assert len(result) == 32
        # Should parse as a valid UUID
        parsed = uuid.UUID(result)
        assert parsed.version == 5

    def test_empty_enclosing_equals_none(self):
        """Explicit empty list should behave identically to None."""
        assert derive_invocation_id("run-1", "loop-1") == derive_invocation_id(
            "run-1", "loop-1", [],
        )

    def test_three_level_nesting(self):
        """All three levels must compose correctly — the deepest loop's
        invocation id incorporates BOTH outer scopes."""
        level_0_id = derive_invocation_id("run-1", "loop-0")
        level_0 = LoopScope("loop-0", level_0_id, iter=0, total=1)

        level_1_id = derive_invocation_id("run-1", "loop-1", [level_0])
        level_1 = LoopScope("loop-1", level_1_id, iter=0, total=1)

        level_2_id = derive_invocation_id(
            "run-1", "loop-2", [level_0, level_1],
        )
        # Changing ANY level should change level_2_id
        alt_level_1 = LoopScope("loop-1", level_1_id, iter=1, total=1)  # different iter
        alt_level_2_id = derive_invocation_id(
            "run-1", "loop-2", [level_0, alt_level_1],
        )
        assert level_2_id != alt_level_2_id


# ─────────────────── Task.get_innermost_scope ──────────────────


class TestTaskGetInnermostScope:
    def test_returns_none_when_not_in_loop(self):
        task = Task(run_id="run-1", node_id="n1")
        assert task.get_innermost_scope() is None

    def test_reads_scope_stack_when_set(self):
        """Phase 4c.4 path — task carries an explicit scope_stack."""
        outer = LoopScope("loop-outer", "inv-o", iter=0, total=2)
        inner = LoopScope("loop-inner", "inv-i", iter=1, total=3)
        task = Task(
            run_id="run-1", node_id="n1",
            scope_stack=[outer, inner],
        )
        innermost = task.get_innermost_scope()
        assert innermost == inner
        # Specifically NOT the outer — tail of the stack wins
        assert innermost is not None
        assert innermost.loop_node_id == "loop-inner"

    @pytest.mark.skip(reason="Legacy loop_context/iteration_index fields removed")
    def test_falls_back_to_legacy_loop_context(self):
        """Legacy tasks with ``loop_context`` + ``iteration_index`` but
        no ``scope_stack`` get a synthesised scope so callers can use
        a single helper during the migration."""
        task = Task(
            run_id="run-1", node_id="n1",
            loop_context={"index": 2, "item": "x", "total": 5},
            iteration_index=2,
        )
        scope = task.get_innermost_scope()
        assert scope is not None
        assert scope.iter == 2
        assert scope.total == 5
        # Legacy scopes have empty loop_node_id and empty invocation_id
        # as sentinels so consumers can detect them.
        assert scope.loop_node_id == ""
        assert scope.invocation_id == ""

    @pytest.mark.skip(reason="Legacy loop_context/iteration_index fields removed")
    def test_legacy_fallback_with_only_iteration_index(self):
        """Some legacy code paths set iteration_index but not loop_context."""
        task = Task(
            run_id="run-1", node_id="n1",
            iteration_index=3,
        )
        scope = task.get_innermost_scope()
        assert scope is not None
        assert scope.iter == 3

    @pytest.mark.skip(reason="Legacy loop_context/iteration_index fields removed")
    def test_scope_stack_takes_precedence_over_legacy_fields(self):
        """If BOTH the new scope_stack and legacy loop_context are set
        (transient state during migration), the scope_stack wins."""
        new_scope = LoopScope("loop-new", "inv-new", iter=10, total=20)
        task = Task(
            run_id="run-1", node_id="n1",
            loop_context={"index": 0, "total": 3},
            iteration_index=0,
            scope_stack=[new_scope],
        )
        assert task.get_innermost_scope() == new_scope


# ─────────────────── Task.with_pushed_scope ────────────────────


class TestTaskWithPushedScope:
    def test_returns_new_task_with_scope_appended(self):
        task = Task(run_id="run-1", node_id="n1")
        scope = LoopScope("loop-1", "inv-1", 0, 3)

        new_task = task.with_pushed_scope(scope)

        assert new_task.scope_stack == [scope]
        # Must be a new task, not the same reference
        assert new_task is not task

    def test_does_not_mutate_source_task(self):
        task = Task(run_id="run-1", node_id="n1")
        scope = LoopScope("loop-1", "inv-1", 0, 3)

        _ = task.with_pushed_scope(scope)

        # Source task's scope_stack must still be None
        assert task.scope_stack is None

    def test_does_not_mutate_source_scope_stack_list(self):
        """Critical immutability guarantee — pushing to a task that
        already has a scope_stack must not mutate the source's list."""
        outer = LoopScope("loop-outer", "inv-o", 0, 2)
        task_with_outer = Task(
            run_id="run-1", node_id="n1", scope_stack=[outer],
        )

        inner = LoopScope("loop-inner", "inv-i", 0, 3)
        nested_task = task_with_outer.with_pushed_scope(inner)

        # Source task still has only the outer scope
        assert len(task_with_outer.scope_stack) == 1
        assert task_with_outer.scope_stack[0] == outer
        # New task has both scopes with outer at position 0
        assert len(nested_task.scope_stack) == 2
        assert nested_task.scope_stack[0] == outer
        assert nested_task.scope_stack[1] == inner
        # The source list and the new list are different objects
        assert task_with_outer.scope_stack is not nested_task.scope_stack

    def test_pushed_scope_is_accessible_via_get_innermost_scope(self):
        """Integration check: the scope pushed via the builder is the
        one get_innermost_scope returns."""
        task = Task(run_id="run-1", node_id="n1")
        scope = LoopScope("loop-1", "inv-1", iter=5, total=10)

        nested = task.with_pushed_scope(scope)
        assert nested.get_innermost_scope() == scope
