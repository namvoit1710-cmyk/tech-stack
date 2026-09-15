"""The iteration-results spill gate has to measure BYTES, not entries.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §15.3.

``_ITERATION_RESULTS_WINDOW`` is 1000 entries. Measured on prod-br10 / tenant-1:

* ``iteration_results`` is **65% of every byte of ``loop_state`` ever stored**
  — 2.61 MB of 4.01 MB across four loops;
* **every ``LoopStarted`` ever written to that tenant — all 23 — has
  ``total <= 100``**, p50 4.

So the count gate could not fire once, while single entries measured in the
hundreds of KB. And spilling was *enabled the whole time*
(``WORKER_CHUNKED_OUTPUT_ENABLED=true``, confirmed by ``cf env``) — it simply
never had a reason to run.

Same defect as ``keeps_inline_output`` ignoring SIZE (SA-1949 / SA-1951), one
layer up: a threshold on count guarding a problem of bytes.
"""
from __future__ import annotations

import json

import pytest

from app.layer2_application.orchestration.loop_driver.event_driven_loop_driver import (
    _ITERATION_RESULTS_MAX_BYTES,
    _ITERATION_RESULTS_WINDOW,
    EventDrivenLoopDriver,
)


def _driver(**kw):
    return EventDrivenLoopDriver(None, **kw)


def _entries(n: int, each_bytes: int = 10):
    return [{"i": i, "blob": "x" * each_bytes} for i in range(n)]


class TestTheShapeProductionActuallyRuns:
    def test_a_hundred_fat_iterations_spill(self):
        """The real shape: max 100 iterations, hundreds of KB each. Under the
        count gate alone this held 2.61 MB in ``loop_state`` forever."""
        d = _driver()
        window = _entries(100, each_bytes=8_000)   # ~800 KB
        assert len(window) < _ITERATION_RESULTS_WINDOW
        assert d._window_is_full(window)

    def test_a_hundred_small_iterations_do_not(self):
        """Loops of this size are the norm — spilling them would trade the bug
        for a file-service call per loop."""
        d = _driver()
        assert not d._window_is_full(_entries(100, each_bytes=10))

    def test_a_single_oversized_entry_trips_it(self):
        d = _driver()
        assert d._window_is_full([{"blob": "y" * (_ITERATION_RESULTS_MAX_BYTES + 1)}])


class TestBothAxesStillWork:
    def test_the_count_gate_is_untouched(self):
        """Byte-cheap but numerous — a million-iteration loop still has to
        spill, which is what the original window was for."""
        d = _driver()
        assert d._window_is_full(_entries(_ITERATION_RESULTS_WINDOW, each_bytes=1))

    def test_just_under_both_is_not_full(self):
        d = _driver()
        window = _entries(_ITERATION_RESULTS_WINDOW - 1, each_bytes=1)
        assert len(json.dumps(window).encode()) < _ITERATION_RESULTS_MAX_BYTES
        assert not d._window_is_full(window)

    @pytest.mark.parametrize("limit", [1024, 64 * 1024])
    def test_the_ceiling_is_configurable(self, limit):
        d = _driver(iteration_results_max_bytes=limit)
        assert d._window_is_full([{"b": "z" * (limit + 100)}])
        assert not d._window_is_full([{"b": "z"}])


class TestItMeasuresRatherThanCrashing:
    def test_a_non_json_value_is_measured_not_fatal(self):
        """``default=str`` mirrors ``inline_output_size_bytes``: a value that is
        not strictly serialisable still has to be MEASURED. The check runs on
        every iteration of every loop — raising here would take the run down."""
        class Exotic:
            def __repr__(self):
                return "exotic"

        d = _driver()
        assert d._window_is_full([{"k": Exotic()}]) in (True, False)  # no raise

    def test_something_that_cannot_be_measured_at_all_counts_as_full(self):
        """Failing closed keeps an exotic value out of ``loop_state`` rather
        than gambling that it fits."""
        class Hostile:
            def __repr__(self):
                raise RuntimeError("nope")

        d = _driver()
        assert d._window_is_full([{"k": Hostile()}]) is True

    def test_an_empty_window_is_never_full(self):
        assert not _driver()._window_is_full([])


class TestTheDefaultMatchesTheExistingCeiling:
    def test_it_is_the_inline_output_ceiling(self):
        """256 KB is where this codebase already decided a value is too big to
        ride an event (``INLINE_OUTPUT_MAX_BYTES``). Picking a different number
        here would mean two answers to one question."""
        from app.layer1_domain.entities.data_reference import INLINE_OUTPUT_MAX_BYTES
        assert _ITERATION_RESULTS_MAX_BYTES == INLINE_OUTPUT_MAX_BYTES


class TestTheSameHoleInTheSiblingHelper:
    """``inline_output_size_bytes`` had the identical defect, on the hot path.

    Its docstring already promised that an unserialisable value "must still be
    MEASURED, not crash the size check" — but it caught only
    ``(TypeError, ValueError)``, and ``default=str`` calls ``repr``. A value
    whose ``__repr__`` raises escaped the clause and propagated out of
    ``should_keep_inline``, which ``complete_task`` calls on every node
    completion. Verified escaping before the fix.
    """

    def test_a_hostile_repr_does_not_escape(self):
        from app.layer1_domain.entities.data_reference import (
            INLINE_OUTPUT_MAX_BYTES,
            inline_output_size_bytes,
        )

        class Hostile:
            def __repr__(self):
                raise RuntimeError("nope")

        assert inline_output_size_bytes({"k": Hostile()}) == INLINE_OUTPUT_MAX_BYTES + 1

    def test_should_keep_inline_survives_it(self):
        """The actual call site: a code node returning something exotic must be
        file-backed, not take the run down."""
        from app.layer1_domain.entities.data_reference import should_keep_inline

        class Hostile:
            def __repr__(self):
                raise RuntimeError("nope")

        assert should_keep_inline("worker:code", {"k": Hostile()}) is False

    def test_ordinary_values_are_unaffected(self):
        from app.layer1_domain.entities.data_reference import (
            inline_output_size_bytes,
            should_keep_inline,
        )
        assert inline_output_size_bytes({"a": 1}) == len(b'{"a": 1}')
        assert should_keep_inline("worker:code", {"a": 1}) is True
