"""SA-1951 / SA-1949 — ``keeps_inline`` must be conditional on SIZE.

The unconditional exemption for ``code`` / ``approval_flow`` outputs broke both
ways once a code node returned 60k rows:

* flag ON  — the worker streamed the big field to a nested file_ref, the CP kept
  the envelope inline, and no resolver owned the resulting shape (SA-1949).
* flag OFF — the raw megabytes rode a NodeOutputProduced event into an Event Mesh
  publish that 413s at ~1 MB (SA-1951).

Both die if an oversized "inline" output is simply file-backed like any other.
"""

from __future__ import annotations

import pytest

from app.layer1_domain.entities.data_reference import (
    INLINE_OUTPUT_MAX_BYTES,
    inline_output_size_bytes,
    keeps_inline_output,
    should_keep_inline,
)


def _small():
    return {"result": [{"sku": "ABC-001", "qty": 12}]}


def _large():
    # ~60k rows, comfortably over the ceiling — the shape from the live repro.
    return {"result": [{"sku": f"SKU-{i:06d}", "qty": i} for i in range(60_000)]}


class TestPredicate:
    def test_type_predicate_is_unchanged(self):
        assert keeps_inline_output("worker:code") is True
        assert keeps_inline_output("code") is True
        assert keeps_inline_output("approval_flow") is True
        assert keeps_inline_output("http_request") is False

    def test_small_inline_output_still_stays_inline(self):
        """The original contract: a small code output round-trips verbatim."""
        assert should_keep_inline("worker:code", _small()) is True

    def test_large_inline_output_is_no_longer_kept_inline(self):
        assert should_keep_inline("worker:code", _large()) is False

    def test_non_inline_type_is_never_kept_inline_however_small(self):
        assert should_keep_inline("http_request", _small()) is False

    def test_boundary_at_the_ceiling(self):
        at_cap = {"blob": "x" * (INLINE_OUTPUT_MAX_BYTES - 20)}
        over_cap = {"blob": "x" * (INLINE_OUTPUT_MAX_BYTES + 20)}
        assert inline_output_size_bytes(at_cap) <= INLINE_OUTPUT_MAX_BYTES
        assert should_keep_inline("code", at_cap) is True
        assert should_keep_inline("code", over_cap) is False

    def test_unserializable_value_fails_closed(self):
        """Unmeasurable ⇒ treat as too large; never gamble it onto the event bus."""
        class Exotic:
            __slots__ = ()

        # default=str makes most things measurable; a key that isn't a valid JSON
        # key type is the honest unmeasurable case.
        value = {("tuple", "key"): Exotic()}
        assert should_keep_inline("code", value) is False


class TestSizeProbe:
    def test_size_is_measured_in_utf8_bytes(self):
        assert inline_output_size_bytes({"a": "x"}) == len('{"a": "x"}'.encode())

    def test_non_json_native_values_are_measured_not_crashed(self):
        from datetime import datetime
        size = inline_output_size_bytes({"when": datetime(2026, 7, 14)})
        assert size > 0


class TestMirrorConsistency:
    """complete_task and the orchestration engine must agree, or the value gets
    re-file-backed behind one of them."""

    @pytest.mark.parametrize("value,expected", [(_small(), True), (_large(), False)])
    def test_both_call_sites_derive_the_same_decision(self, value, expected):
        # complete_task's decision (inline_type AND size), spelled out as the code does.
        inline_type = keeps_inline_output("worker:code")
        complete_task_decision = (
            inline_type and inline_output_size_bytes(value) <= INLINE_OUTPUT_MAX_BYTES
        )
        # the engine's decision
        engine_decision = should_keep_inline("worker:code", value)

        assert complete_task_decision == engine_decision == expected
