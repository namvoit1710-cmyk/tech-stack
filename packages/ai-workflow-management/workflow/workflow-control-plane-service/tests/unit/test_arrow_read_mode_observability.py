"""SA-1905 — the sidecar's read-mode plumbing must be observable, and its size
floor must be its own.

Both properties here were found the expensive way, on a live shadow soak that
measured nothing and looked exactly like a clean run:

1.  ``_try_materialize_arrow`` returned ``_ARROW_MISS`` without counting
    anything whenever the ref was ineligible. So "the flag is off", "no sidecar
    was ever written", and "the read never reached materialize" all presented
    as an ABSENT ``arrow_read_total`` — which is also what "zero mismatches"
    looks like. An observability feature whose quiet state is ambiguous is
    worse than none.

2.  ``_arrow_worth_writing`` borrowed ``CHUNKING_THRESHOLD_BYTES`` (1 MB) as
    its floor. That is an EMPTY window on the live topology: a worker output
    that large fails the Event Mesh broker cap (1 048 576 bytes) before it ever
    reaches ``store_data``, so nothing could be simultaneously big enough to
    earn a sidecar and small enough to arrive.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.layer2_application.data.data_store_service import (
    DataStoreService,
    MaterializeTooLargeError,
    _ARROW_MISS,
)


class _Monitor:
    """Records what a dashboard would actually see."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def increment(self, name: str, tags: dict | None = None) -> None:
        self.calls.append((name, dict(tags or {})))

    def outcomes(self) -> list[tuple[str, str]]:
        return [
            (t.get("outcome", ""), t.get("cause", ""))
            for n, t in self.calls
            if n == "arrow_read_total"
        ]


def _svc(monitor=None, **kw) -> DataStoreService:
    svc = DataStoreService.__new__(DataStoreService)
    svc._monitor = monitor
    svc._arrow_read_mode = kw.pop("arrow_read_mode", "shadow")
    svc._arrow_min_bytes = kw.pop("arrow_min_bytes", 1024 * 1024)
    svc._chunking_threshold = kw.pop("chunking_threshold", 1024 * 1024)
    svc._arrow_write_enabled = kw.pop("arrow_write_enabled", True)
    wheel = kw.pop("wheel", True)
    svc._arrow_available = lambda: wheel
    for k, v in kw.items():
        setattr(svc, k, v)
    return svc


def _ref(**over) -> dict:
    ref = {
        "__file_ref": True,
        "file_id": "csv-1",
        "content_type": "text/csv",
        "arrow_file_id": "arrow-1",
        "arrow_size_bytes": 4096,
    }
    ref.update(over)
    return ref


# ---------------------------------------------------------------------------
# 1. every reason a read skips the sidecar is a NAMED reason
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mutate,expected", [
    ({}, ""),                                            # eligible
    ({"arrow_file_id": ""}, "no_sidecar"),
    ({"arrow_file_id": None}, "no_sidecar"),
    ({"content_type": "application/json"}, "not_csv"),
    ({"content_type": ""}, "not_csv"),
    ({"content_type": None}, "not_csv"),
])
def test_ineligible_cause_names_the_reason(mutate, expected):
    svc = _svc()
    svc._arrow_available = lambda: True
    assert svc._arrow_ineligible_cause(_ref(**mutate)) == expected


def test_read_disabled_and_wheel_missing_are_distinct_causes():
    off = _svc(arrow_read_mode="off")
    off._arrow_available = lambda: True
    assert off._arrow_ineligible_cause(_ref()) == "read_disabled"

    # Mode is on but the native wheel did not make it into the image — a
    # deployment fault, not a configuration choice. It must not be reported
    # as "the feature is off".
    no_wheel = _svc(arrow_read_mode="shadow")
    no_wheel._arrow_available = lambda: False
    assert no_wheel._arrow_ineligible_cause(_ref()) == "wheel_missing"


@pytest.mark.asyncio
async def test_ineligible_read_emits_a_counted_skip():
    """The regression this file exists for: a skip used to be silent."""
    mon = _Monitor()
    svc = _svc(monitor=mon)
    svc._arrow_available = lambda: True

    got = await svc._try_materialize_arrow(_ref(arrow_file_id=""), 10_000, None)

    assert got is _ARROW_MISS
    assert mon.outcomes() == [("skip", "no_sidecar")]


@pytest.mark.asyncio
async def test_read_disabled_is_the_one_skip_that_stays_quiet():
    """`off` is every deployment that has not opted in. Counting it would put a
    permanent, meaningless series on every dashboard in the estate."""
    mon = _Monitor()
    svc = _svc(monitor=mon, arrow_read_mode="off")
    svc._arrow_available = lambda: True

    assert await svc._try_materialize_arrow(_ref(), 10_000, None) is _ARROW_MISS
    assert mon.outcomes() == []


@pytest.mark.asyncio
async def test_wheel_missing_is_loud():
    mon = _Monitor()
    svc = _svc(monitor=mon)
    svc._arrow_available = lambda: False

    assert await svc._try_materialize_arrow(_ref(), 10_000, None) is _ARROW_MISS
    assert mon.outcomes() == [("skip", "wheel_missing")]


def test_a_missing_monitor_never_raises():
    """Telemetry is best-effort; the read path must not depend on it."""
    svc = _svc(monitor=None)
    svc._arrow_outcome("skip", "no_sidecar")   # must not raise

    boom = MagicMock()
    boom.increment.side_effect = RuntimeError("statsd down")
    _svc(monitor=boom)._arrow_outcome("mismatch")


# ---------------------------------------------------------------------------
# 2. the write floor is ARROW_SIDECAR_MIN_BYTES, not the streaming threshold
# ---------------------------------------------------------------------------

ROWS = [{"id": i, "sku": "SKU-%06d" % i, "zip": "03230"} for i in range(200)]


def test_min_bytes_is_independent_of_the_streaming_threshold():
    """Borrowing CHUNKING_THRESHOLD_BYTES made the sidecar unreachable in
    practice. Lowering the sidecar's floor must NOT lower the streaming one,
    and raising the streaming one must NOT silence the sidecar."""
    svc = _svc(arrow_min_bytes=0, chunking_threshold=1024 * 1024)
    assert svc._arrow_worth_writing(ROWS) is True

    svc = _svc(arrow_min_bytes=10 * 1024 * 1024, chunking_threshold=0)
    assert svc._arrow_worth_writing(ROWS) is False


def test_zero_min_bytes_writes_a_sidecar_for_every_value():
    svc = _svc(arrow_min_bytes=0)
    assert svc._arrow_worth_writing([{"a": 1}]) is True
    assert svc._arrow_worth_writing([]) is False   # nothing to write


def test_default_min_bytes_still_falls_back_to_the_chunking_threshold():
    """Back-compat: an environment that sets neither behaves as it did before."""
    from app.layer4_frameworks.config.app_config import Settings

    s = Settings()
    assert s.ARROW_SIDECAR_MIN_BYTES == s.CHUNKING_THRESHOLD_BYTES


# ---------------------------------------------------------------------------
# 3. shadow mode reports, and changes nothing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("arrow,csv,outcome,cause", [
    ({"a": 1}, {"a": 1}, "shadow_match", ""),
    ([1, 2, 3], [1, 2, 3], "shadow_match", ""),
    # 1 == 1.0 in Python. A comparator built on `==` would report a match here
    # and certify the exact Float64 divergence shadow mode exists to find.
    ({"a": 1}, {"a": 1.0}, "mismatch", "int_vs_float"),
    ({"a": True}, {"a": 1}, "mismatch", "type_mismatch"),
    ({"a": ""}, {"a": None}, "mismatch", "empty_string_vs_null"),
    ({"z": "03230"}, {"z": 3230}, "mismatch", "string_vs_number"),
])
def test_shadow_compare_reports_the_right_outcome(arrow, csv, outcome, cause):
    mon = _Monitor()
    _svc(monitor=mon)._shadow_compare(_ref(), arrow, csv)
    assert mon.outcomes() == [(outcome, cause)]


def test_shadow_compare_returns_nothing_and_mutates_nothing():
    """In shadow the CSV value wins, always. This is what makes the flag safe
    to turn on in front of real traffic."""
    mon = _Monitor()
    csv = {"rows": [{"a": 1}]}
    before = repr(csv)

    assert _svc(monitor=mon)._shadow_compare(_ref(), {"rows": []}, csv) is None
    assert repr(csv) == before
    assert mon.outcomes() == [("mismatch", "length_mismatch")]


# ---------------------------------------------------------------------------
# 4. the attempt runs in FRONT of every CSV branch, not among them
# ---------------------------------------------------------------------------

REFS_WITH_ARTIFACTS = [
    pytest.param(
        {"artifacts": [{"artifact_type": "nested_array", "file_id": "c1",
                        "name": "rows", "path": "rows"}]},
        id="nested_array",
    ),
    pytest.param(
        {"artifacts": [{"artifact_type": "loop_chunk", "file_id": "c1",
                        "size_bytes": 10}]},
        id="loop_chunk",
    ),
    pytest.param(
        {"artifacts": [{"artifact_type": "array_field", "file_id": "c1",
                        "name": "rows"}]},
        id="array_field",
    ),
    pytest.param({"artifacts": []}, id="plain_csv"),
    pytest.param({"file_id": "", "artifacts": []}, id="empty_value"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", REFS_WITH_ARTIFACTS)
async def test_the_sidecar_is_consulted_on_every_branch(shape):
    """The regression that made the live soak measure nothing.

    `_try_materialize_arrow` used to sit BELOW the `loop_chunk` and
    `nested_array` branches, both of which `return`. So on a ref with
    artifacts — the one shape nested Arrow exists for — it was unreachable,
    and `arrow_read_total` stayed empty in a way that reads as "no mismatches".
    """
    seen: list[dict] = []
    svc = _svc()
    svc._record_full_materialize = lambda *a, **k: None

    async def _spy(ref, max_bytes, context):
        seen.append(ref)
        return _ARROW_MISS

    async def _csv(ref, max_bytes, context):
        return {"from": "csv"}

    svc._try_materialize_arrow = _spy
    svc._materialize_csv = _csv

    out = await svc.materialize(_ref(**shape), 0, "n.result")

    assert len(seen) == 1, "the sidecar was never consulted on this branch"
    assert out == {"from": "csv"}, "shadow mode must return the CSV value"


@pytest.mark.asyncio
async def test_shadow_returns_csv_even_when_arrow_disagrees():
    mon = _Monitor()
    svc = _svc(monitor=mon)
    svc._record_full_materialize = lambda *a, **k: None

    async def _arrow(ref, max_bytes, context):
        return {"n": 1.0}

    async def _csv(ref, max_bytes, context):
        return {"n": 1}

    svc._try_materialize_arrow = _arrow
    svc._materialize_csv = _csv

    out = await svc.materialize(_ref(), 0, "n.result")

    assert out == {"n": 1} and isinstance(out["n"], int)
    assert mon.outcomes() == [("mismatch", "int_vs_float")]


@pytest.mark.asyncio
async def test_read_mode_on_returns_the_arrow_value_and_skips_csv():
    csv_ran = []
    svc = _svc(arrow_read_mode="on")
    svc._record_full_materialize = lambda *a, **k: None

    async def _arrow(ref, max_bytes, context):
        return {"from": "arrow"}

    async def _csv(ref, max_bytes, context):
        csv_ran.append(1)
        return {"from": "csv"}

    svc._try_materialize_arrow = _arrow
    svc._materialize_csv = _csv

    assert await svc.materialize(_ref(), 0, "n.result") == {"from": "arrow"}
    assert csv_ran == [], "read_mode=on must not also download the CSV"


@pytest.mark.asyncio
async def test_a_non_ref_is_returned_untouched():
    svc = _svc()
    svc._record_full_materialize = lambda *a, **k: None
    assert await svc.materialize({"plain": "dict"}, 0, "n") == {"plain": "dict"}


# ---------------------------------------------------------------------------
# 5. naming the divergence — 84% of shadow reads mismatch, and "mismatch"
#    alone does not say whether that is 1 vs 1.0 or "" vs null
# ---------------------------------------------------------------------------

from app.layer2_application.data.data_store_service import _divergence_kind  # noqa: E402


@pytest.mark.parametrize(("arrow", "csv", "kind"), [
    # The two that survive on the control plane's own read path — measured over
    # 25 realistic shapes WITH column_types supplied. Both are CSV being unable
    # to represent emptiness, and Arrow is right in both.
    ({"s": ""}, {"s": None}, "empty_string_vs_null"),
    ({"s": None}, {"s": ""}, "empty_string_vs_null"),
    ({"a": 1, "late": ""}, {"a": 1, "late": None}, "empty_string_vs_null"),
    ({"o": {}}, {}, "dropped_empty_container"),
    ({"t": []}, {}, "dropped_empty_container"),
    # Only reachable when column_types is ABSENT — pinned so a regression that
    # stops passing the types shows up as a NAMED cause instead of noise.
    ({"s": "42"}, {"s": 42}, "string_vs_number"),
    ({"z": "03230"}, {"z": 3230}, "string_vs_number"),
    # The Float64 trap the comparator exists to catch.
    ({"n": 1}, {"n": 1.0}, "int_vs_float"),
    # Everything else, so the label set stays closed.
    ({"x": None}, {"x": 5}, "null_vs_value"),
    ({"a": 1}, {"b": 1}, "missing_key"),
    ({"a": 1}, {"a": 2}, "value_mismatch"),
    ({"a": [1, 2]}, {"a": [1]}, "length_mismatch"),
    ({"b": True}, {"b": 1}, "type_mismatch"),
])
def test_the_divergence_is_named(arrow, csv, kind):
    assert _divergence_kind(arrow, csv) == kind


def test_nesting_is_walked_to_the_leaf_that_differs():
    a = {"rows": [{"id": 1, "meta": {"note": ""}}]}
    c = {"rows": [{"id": 1, "meta": {"note": None}}]}
    assert _divergence_kind(a, c) == "empty_string_vs_null"


def test_the_walk_is_deterministic_not_dict_order():
    """One label per mismatch, and the same label every time — otherwise the
    distribution the metric is for would be an artefact of key ordering."""
    a = {"z": "", "a": 1}
    c = {"a": 2, "z": None}
    assert _divergence_kind(a, c) == _divergence_kind(dict(reversed(a.items())), c)
    # "a" sorts before "z", so the int difference is what is reported
    assert _divergence_kind(a, c) == "value_mismatch"


def test_a_classifier_that_cannot_decide_says_so_rather_than_guessing():
    assert _divergence_kind({}, {}) == "unknown"
    assert _divergence_kind([], []) == "unknown"


def test_classification_never_raises():
    """It runs after the read already decided to return the CSV value. A
    classifier that throws turns an observation into an incident."""
    class _Hostile:
        def __eq__(self, other):
            raise RuntimeError("no equality for you")

        def __repr__(self):
            raise RuntimeError("no repr either")

    assert isinstance(_divergence_kind({"a": _Hostile()}, {"a": 1}), str)

    deep_a: dict = {"v": ""}
    deep_c: dict = {"v": None}
    for _ in range(200):
        deep_a = {"n": deep_a}
        deep_c = {"n": deep_c}
    assert _divergence_kind(deep_a, deep_c) == "unknown"   # bounded, not a crash


def test_the_mismatch_counter_carries_the_cause():
    mon = _Monitor()
    _svc(monitor=mon)._shadow_compare(_ref(), {"s": ""}, {"s": None})
    assert mon.outcomes() == [("mismatch", "empty_string_vs_null")]


def test_a_match_carries_no_cause():
    mon = _Monitor()
    _svc(monitor=mon)._shadow_compare(_ref(), {"s": "x"}, {"s": "x"})
    assert mon.outcomes() == [("shadow_match", "")]


# ---------------------------------------------------------------------------
# 6. hit == shadow_match + mismatch + compare_skipped, by construction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_hit_always_gets_a_verdict_even_when_the_csv_side_fails():
    """Otherwise `hit` drifts above the verdicts and the ratio read off them is
    wrong. The 84% figure was computed from `mismatch / hit` on the assumption
    that they reconcile — they did, but nothing made them."""
    mon = _Monitor()
    svc = _svc(monitor=mon)
    svc._record_full_materialize = lambda *a, **k: None

    async def _arrow(ref, max_bytes, context):
        svc._arrow_outcome("hit")
        return {"n": 1}

    async def _csv(ref, max_bytes, context):
        raise RuntimeError("file service refused the download")

    svc._try_materialize_arrow = _arrow
    svc._materialize_csv = _csv

    with pytest.raises(RuntimeError):
        await svc.materialize(_ref(), 0, "n.result")

    assert mon.outcomes() == [("hit", ""), ("compare_skipped", "csv_failed")]


@pytest.mark.asyncio
async def test_the_csv_failure_still_propagates():
    """Counting the skip must not swallow it — the caller's fallback depends on
    the raise."""
    svc = _svc(monitor=_Monitor())
    svc._record_full_materialize = lambda *a, **k: None

    async def _arrow(ref, max_bytes, context):
        return {"n": 1}

    async def _csv(ref, max_bytes, context):
        raise MaterializeTooLargeError("n.result", 99, 10)

    svc._try_materialize_arrow = _arrow
    svc._materialize_csv = _csv

    with pytest.raises(MaterializeTooLargeError):
        await svc.materialize(_ref(), 0, "n.result")


@pytest.mark.asyncio
async def test_nothing_is_counted_when_there_was_no_sidecar_to_compare():
    """A CSV failure with no arrow value is an ordinary error, not a shadow
    event — counting it would inflate the denominator with reads that never
    happened."""
    mon = _Monitor()
    svc = _svc(monitor=mon)
    svc._record_full_materialize = lambda *a, **k: None

    async def _arrow(ref, max_bytes, context):
        return _ARROW_MISS

    async def _csv(ref, max_bytes, context):
        raise RuntimeError("boom")

    svc._try_materialize_arrow = _arrow
    svc._materialize_csv = _csv

    with pytest.raises(RuntimeError):
        await svc.materialize(_ref(), 0, "n.result")
    assert mon.outcomes() == []


# ---------------------------------------------------------------------------
# 7. the label set must be CONSTANT, against the real Prometheus monitor
# ---------------------------------------------------------------------------

def _emitted(monitor, name):
    from prometheus_client import generate_latest
    return sorted({
        line.split(" ")[0]
        for line in generate_latest(monitor._registry).decode().splitlines()
        if line.startswith(name + "{")
    })


def test_a_labelled_outcome_is_not_silently_dropped():
    """`PrometheusMonitor._get_counter` freezes a metric's label set on the
    FIRST call and returns None — silently — for every later call whose keys
    differ. `hit` carries no cause, so it won the race and every labelled
    outcome after it went on the floor.

    This is not theory: a live probe recorded `hit` with no verdict at all, and
    it is why no `skip` cause has ever been seen in production. The test drives
    the REAL monitor, because a fake one records whatever it is handed and
    would have shown this working.
    """
    from app.layer4_frameworks.monitoring.prometheus import PrometheusMonitor

    monitor = PrometheusMonitor()
    svc = _svc(monitor=monitor)
    svc._arrow_available = lambda: True

    svc._arrow_outcome("hit")                                  # no cause
    svc._arrow_outcome("mismatch", "empty_string_vs_null")     # with cause
    svc._arrow_outcome("skip", "no_sidecar")

    emitted = _emitted(monitor, "arrow_read_total")
    assert len(emitted) == 3, emitted
    assert any("empty_string_vs_null" in e for e in emitted), emitted
    assert any("no_sidecar" in e for e in emitted), emitted


def test_the_native_split_counter_has_the_same_constant_shape():
    """Same trap, same shape: `native` carries no cause and `fallback` does, so
    every fallback would have been dropped while the successes were recorded —
    which reads exactly like "it never fails"."""
    from app.layer4_frameworks.monitoring.prometheus import PrometheusMonitor

    monitor = PrometheusMonitor()
    svc = _svc(monitor=monitor)

    svc._split_outcome("native")
    svc._split_outcome("fallback", "TimeoutError")

    emitted = _emitted(monitor, "native_split_total")
    assert len(emitted) == 2, emitted
    assert any("TimeoutError" in e for e in emitted), emitted


def test_every_arrow_outcome_carries_both_label_keys():
    mon = _Monitor()
    svc = _svc(monitor=mon)
    svc._arrow_available = lambda: True
    svc._arrow_outcome("hit")
    svc._arrow_outcome("mismatch", "int_vs_float")
    assert [set(t) for _n, t in mon.calls] == [{"outcome", "cause"}, {"outcome", "cause"}]


# ---------------------------------------------------------------------------
# 8. the decision: "" reads back as ""
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(("arrow", "csv", "expected"), [
    # The decision, 2026-08-09. CSV cannot tell an empty cell from a null one
    # and returns null for a value the node actually produced as "". Arrow
    # carries the type in the file.
    ({"note": ""}, {"note": None}, {"note": ""}),
    ({"rows": [{"note": ""}, {"note": "n1"}]},
     {"rows": [{"note": None}, {"note": "n1"}]},
     {"rows": [{"note": ""}, {"note": "n1"}]}),
    # Same family: an empty container CSV drops entirely.
    ({"meta": {}}, {}, {"meta": {}}),
])
async def test_read_mode_on_returns_the_value_the_node_produced(arrow, csv, expected):
    svc = _svc(arrow_read_mode="on")
    svc._record_full_materialize = lambda *a, **k: None

    async def _arrow(ref, max_bytes, context):
        return arrow

    async def _csv(ref, max_bytes, context):
        return csv

    svc._try_materialize_arrow = _arrow
    svc._materialize_csv = _csv

    assert await svc.materialize(_ref(), 0, "n.result") == expected


@pytest.mark.asyncio
async def test_a_failed_arrow_read_silently_restores_the_old_reading():
    """Pinned because it is the sharp edge of `on`, not because it is wanted.

    Under `shadow` a fallback cost nothing — the CSV value was being returned
    anyway. Under `on` the same fallback changes the value back to null, with no
    error anywhere. That is why `arrow_read_total{outcome="fallback"}` stops
    being a performance signal and becomes a correctness one.
    """
    svc = _svc(arrow_read_mode="on")
    svc._record_full_materialize = lambda *a, **k: None

    async def _arrow(ref, max_bytes, context):
        return _ARROW_MISS          # the read failed; the cause was counted

    async def _csv(ref, max_bytes, context):
        return {"note": None}

    svc._try_materialize_arrow = _arrow
    svc._materialize_csv = _csv

    assert await svc.materialize(_ref(), 0, "n.result") == {"note": None}


@pytest.mark.asyncio
async def test_shadow_still_returns_the_csv_reading():
    """The other side of the switch: nothing about `shadow` changed, so an
    environment that has not opted in reads exactly what it read before."""
    svc = _svc(arrow_read_mode="shadow", monitor=_Monitor())
    svc._record_full_materialize = lambda *a, **k: None

    async def _arrow(ref, max_bytes, context):
        return {"note": ""}

    async def _csv(ref, max_bytes, context):
        return {"note": None}

    svc._try_materialize_arrow = _arrow
    svc._materialize_csv = _csv

    assert await svc.materialize(_ref(), 0, "n.result") == {"note": None}
