"""A window must refuse rows it did not ask for.

Run 56e11b2f (2026-08-19). ``_fetch_rows_filtered`` sends

    __parent_row_id ge {lo} and __parent_row_id lt {hi + 1}

and the file service parsed that conjunction into ``QueryAndFilter.where``, a dict
keyed by COLUMN — so the second clause REPLACED the first and the service answered
``lt hi+1``: every row from 0 up to hi. Correctness survived (the join groups by
``__parent_row_id``, so unmatched rows attach to nobody) but the BYTES did not. Each
window re-read whole child tables, so the SA-1943 budget was spent on the payload
instead of on a window, and window 9 of a 5 000-row tree fetched 67 897 568 B against
a 67 108 864 B cap.

**Why `test_windowed_tree_join.py` stayed green through all of it:** its
``_FilteringClient`` honours the compound filter — it splits on `" and "` and applies
BOTH bounds. That fake is a more capable server than the real one. Its docstring says
the filter was *"verified live against the tenant-1 file service: `$filter=
__parent_row_id ge 5000` really does start at 5000"* — the SINGLE-clause form was
verified, and the fake then modelled the COMPOUND form nobody tested. The fake encoded
an assumption instead of a measurement.

So the fake below is deliberately the opposite: it reproduces the server as it actually
behaved, and the guard has to catch it. Both fakes are kept — one is the spec of a
correct service, one is the adversary.

Ship order this pins: the FILE SERVICE fix deploys first. With an unfixed service every
windowed read now fails here loudly, by design, instead of silently costing a payload.
"""
from __future__ import annotations

import json

import pytest

from tests.unit.test_file_ref_rejoin import _build
from worker_sdk.layer4_frameworks.providers.file_service.http_file_ref_resolver import (
    HttpFileRefResolver,
)
from worker_sdk.layer1_domain.exceptions import FileRefResolutionError


class _FakeResponse:
    def __init__(self, rows):
        self._payload = {"data": {"data": rows}}
        self.content = json.dumps(self._payload).encode("utf-8")

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _LastClauseWinsClient:
    """The file service as it actually behaved before the fix.

    ``_parse_and_filter`` collected predicates into ``where[field] = predicate``, so
    two clauses on one column collapsed to the LAST one. Measured live on tenant-1:
    ``ge 8000 and lt 8500`` returned row 0 onward, and REVERSING the clauses returned
    8000 onward — order deciding the result is the fingerprint of last-write-wins.
    """

    def __init__(self, store):
        self._store = store
        self.requests = []

    def get(self, url, params=None):
        file_id = url.split("/odata/")[1].split("/data")[0]
        params = params or {}
        self.requests.append((file_id, dict(params)))
        rows = list(self._store.get(file_id, []))

        flt = params.get("$filter")
        if flt:
            surviving = {}                       # field -> (op, value); later WINS
            for token in flt.split(" and "):
                bits = token.split()
                if len(bits) == 3:
                    surviving[bits[0]] = (bits[1], int(bits[2]))
            for field, (op, value) in surviving.items():
                def keep(r, field=field, op=op, value=value):
                    v = r.get(field)
                    if v in (None, ""):
                        return False
                    v = int(v)
                    return v >= value if op == "ge" else v < value
                rows = [r for r in rows if keep(r)]

        skip = int(params.get("$skip", 0) or 0)
        top = params.get("$top")
        rows = rows[skip:]
        if top is not None:
            rows = rows[: int(top)]
        return _FakeResponse(rows)


def _resolver(store, client_cls):
    r = HttpFileRefResolver("http://fs:8000")
    r._client = client_cls(store)
    return r


# A tree shaped like the failing run: many parents, one child row each, so a late
# window's `lt hi+1` matches every child row in the table.
_TREE = [{"id": i, "kids": [{"k": i}]} for i in range(20)]


def test_a_widened_window_is_refused_not_consumed():
    store = {}
    ref = _build(store, _TREE)
    r = _resolver(store, _LastClauseWinsClient)

    with pytest.raises(FileRefResolutionError) as exc:
        list(r.iter_objects(ref, window_size=5))

    message = str(exc.value)
    assert "did not honour" in message
    assert "__parent_row_id" in message


def test_the_guard_names_the_window_it_asked_for():
    """The operator must be able to see the window from the message alone — the
    production error named only a byte count and a child file id, which read as
    'the input is too big' when the input was 0.9 MB."""
    store = {}
    ref = _build(store, _TREE)
    r = _resolver(store, _LastClauseWinsClient)

    with pytest.raises(FileRefResolutionError) as exc:
        list(r.iter_objects(ref, window_size=5))

    assert "[5, 9]" in str(exc.value) or "[0, 4]" in str(exc.value)


def test_a_correct_service_raises_nothing():
    """No false positive: the guard must be invisible against a service that filters."""
    from tests.unit.test_windowed_tree_join import _FilteringClient

    store = {}
    ref = _build(store, _TREE)
    windowed = list(_resolver(store, _FilteringClient).iter_objects(ref, window_size=5))
    whole = _resolver(store, _FilteringClient).resolve_inputs(ref)

    assert windowed == whole
    assert len(windowed) == 20


def test_first_window_is_guarded_too():
    """`lo == 0` is the trap: `ge 0 and lt W` degenerates to `lt W`, which is
    ACCIDENTALLY the right predicate, so window 0 looks healthy while every later
    window is broken. The guard must not be lulled by it — later windows still raise."""
    store = {}
    ref = _build(store, _TREE)
    r = _resolver(store, _LastClauseWinsClient)

    windows = r.iter_root_windows(ref, window_size=5)
    first = next(iter(windows))
    assert len(first) == 5                        # window 0 is genuinely fine

    with pytest.raises(FileRefResolutionError):
        list(windows)                             # window 1 onward is not
