"""A large file-backed array is handed over as a HANDLE, not materialised.

Step 2 of streaming a file-backed body. Step 1 (the windowed join) is in
``test_windowed_tree_join``; this is the part that decides a collection is never
turned into one big Python object in the first place.

The payoff is the last test in the first class: a collection whose TOTAL wire
bytes exceed ``RESOLVE_MAX_INPUT_BYTES`` streams anyway. That cap exists to stop
the whole collection landing in a small pod's RAM — once it never lands, the cap
stops being the thing that decides whether a payload can be sent. It is kept as a
PER-WINDOW guard, so one absurd window still refuses.

OFF by default: a handle where the other nine workers expect a list would be a
silent contract change, so only a worker that opts in ever sees one.
"""

from __future__ import annotations

import json

import pytest

from tests.unit.test_windowed_tree_join import _FilteringClient
from tests.unit.test_file_ref_rejoin import _build
from worker_sdk.layer4_frameworks.providers.file_service.http_file_ref_resolver import (
    HttpFileRefResolver,
    StreamedCollection,
)


def _resolver(store, **kw):
    r = HttpFileRefResolver("http://fs:8000", **kw)
    r._client = _FilteringClient(store)
    return r


_ROWS = [{"id": i, "kids": [{"k": i}]} for i in range(8)]


class TestHandOverAHandle:
    def test_off_by_default_the_value_is_materialised_as_before(self):
        store = {}
        ref = _build(store, _ROWS)
        out = _resolver(store).resolve_inputs({"body": ref})

        assert isinstance(out["body"], list)
        assert len(out["body"]) == 8

    def test_on_a_large_array_becomes_a_handle(self):
        store = {}
        ref = _build(store, _ROWS)
        out = _resolver(store, stream_collections=True).resolve_inputs({"body": ref})

        assert isinstance(out["body"], StreamedCollection)

    def test_iterating_the_handle_equals_materialising_it(self):
        """Same contract as the windowed join: the handle is not a new document."""
        store = {}
        ref = _build(store, _ROWS)
        whole = _resolver(store).resolve_inputs({"body": ref})["body"]
        handle = _resolver(store, stream_collections=True).resolve_inputs({"body": ref})["body"]

        assert list(handle) == whole

    def test_an_object_root_is_still_materialised(self):
        """One object is one object; a handle buys nothing and complicates every
        caller that just wants a dict."""
        store = {}
        ref = _build(store, {"id": 1, "kids": [{"k": 1}]})
        out = _resolver(store, stream_collections=True).resolve_inputs({"body": ref})

        assert isinstance(out["body"], dict)

    def test_a_collection_over_the_input_cap_streams_anyway(self):
        """The point of the exercise.

        The same ref refuses when materialised (the whole thing would land in
        RAM) and streams when it does not. The cap survives as a per-window
        guard, not as a ceiling on the payload.
        """
        store = {}
        ref = _build(store, [{"id": i, "text": "x" * 400} for i in range(40)])
        cap = 3000  # smaller than the whole collection, larger than one window

        from worker_sdk.layer1_domain.exceptions import FileRefResolutionError
        with pytest.raises(FileRefResolutionError, match="input cap"):
            _resolver(store, max_input_bytes=cap).resolve_inputs({"body": ref})

        streamed = _resolver(
            store, max_input_bytes=cap, stream_collections=True, window_size=4,
        ).resolve_inputs({"body": ref})
        assert len(list(streamed["body"])) == 40

    def test_one_absurd_window_still_refuses(self):
        """Per-window, not per-payload — but still a guard."""
        store = {}
        ref = _build(store, [{"id": i, "text": "x" * 400} for i in range(40)])

        from worker_sdk.layer1_domain.exceptions import FileRefResolutionError
        handle = _resolver(
            store, max_input_bytes=500, stream_collections=True, window_size=40,
        ).resolve_inputs({"body": ref})["body"]
        with pytest.raises(FileRefResolutionError, match="input cap"):
            list(handle)


class TestHandleShape:
    def test_it_is_iterable_more_than_once(self):
        """The worker may need to size it and then send it."""
        store = {}
        ref = _build(store, _ROWS)
        handle = _resolver(store, stream_collections=True).resolve_inputs({"body": ref})["body"]

        assert list(handle) == list(handle)

    def test_it_carries_the_ref_it_came_from(self):
        store = {}
        ref = _build(store, _ROWS)
        handle = _resolver(store, stream_collections=True).resolve_inputs({"body": ref})["body"]

        assert handle.ref["file_id"] == ref["file_id"]

    def test_it_does_not_pretend_to_be_a_list(self):
        """``json.dumps`` must FAIL rather than silently emit something wrong —
        a caller that has not been taught to stream should break loudly."""
        store = {}
        ref = _build(store, _ROWS)
        handle = _resolver(store, stream_collections=True).resolve_inputs({"body": ref})["body"]

        with pytest.raises(TypeError):
            json.dumps(handle)
