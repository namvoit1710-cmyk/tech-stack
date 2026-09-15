"""A missing key on a file-backed value must describe the DATA, not the envelope.

The resolver chain is ``FileBacked -> Metadata -> Traversal``
(``bootstrap.py:675``). When the file-backed strategy cannot find a path in the
materialized CONTENT it returns ``UNRESOLVED``, and the chain falls through to
``TraversalResolverStrategy``, which walks the raw **file_ref envelope**. Three
outcomes came out of that, and only the first is intended:

    envelope = {__file_ref, file_id:"F-123", row_count:1, columns:["__row_id"],
                version:"envelope-v9", size_bytes:11, artifacts:[]}

    {{$node.file_id}} -> "F-123"        intended: 39 call sites, the data-edit
                                        node's whole contract
    {{$node.version}} -> "envelope-v9"  a DATA field whose name collides with a
                                        reference field silently reads METADATA
    {{$node.reqID}}   -> "Key 'reqID' not found in dict. Available keys:
                          ['__file_ref','arrow_file_id','etag',…]"

The third cost real time on tenant-1 (2026-08-11). Run ``18847bb8`` failed with
that message; the true cause was two nodes upstream — the Claude node
"3. Extract reqID" returned an EMPTY object for MCKES-360 (``size_bytes=11``,
``columns=['__row_id']``) where all 12 previous runs had ``153-215 B`` and 9
columns including ``reqID``. The message named ``arrow_file_id`` and ``etag``,
which reads like an Arrow/claim-check fault, so the first hour of diagnosis went
into the storage layer instead of at the empty upstream output.

The envelope already carries ``columns`` — the data's real field list — so the
right answer was available at the point the wrong one was printed.

This module pins:
  1. plain dicts keep the old message exactly (no collateral change);
  2. a file_ref reports its DATA fields, never its reference fields;
  3. ``columns == ['__row_id']`` is named for what it is: the upstream node
     produced nothing;
  4. the message stays bounded as ``columns`` grows;
  5. ``{{$node.file_id}}`` still resolves, and every OTHER bare reference read is
     counted + logged (shadow) so the decision to refuse it is taken on numbers.
"""

from __future__ import annotations

import logging

import pytest

from app.layer1_domain.value_objects.csv_document import ROW_ID_COLUMN
from app.layer2_application.resolve.variable_resolver import (
    TraversalResolverStrategy,
    VariableResolutionError,
)

pytestmark = pytest.mark.unit


class _Monitor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def increment(self, metric, tags=None):
        self.calls.append((metric, tags or {}))


def _envelope(**over) -> dict:
    """A file_ref exactly as ``build_file_ref`` writes it — the key set is the
    one observed live on tenant-1 run 18847bb8."""
    env = {
        "__file_ref": True,
        "arrow_file_id": "A-1",
        "arrow_size_bytes": 96,
        "artifact_id": "",
        "artifacts": [],
        "column_types": {},
        "columns": [ROW_ID_COLUMN, "status", "reqID", "raw", "error"],
        "content_type": "text/csv",
        "etag": "W/\"9\"",
        "file_id": "F-123",
        "item_kind": "",
        "row_count": 1,
        "schema_hash": "h",
        "size_bytes": 153,
        "value_kind": "object",
        "version": "envelope-v9",
    }
    env.update(over)
    return env


# Reference fields that must NEVER be offered to the author as if they were data.
_ENVELOPE_ONLY = (
    "arrow_file_id", "arrow_size_bytes", "artifact_id", "column_types",
    "content_type", "etag", "schema_hash", "size_bytes", "value_kind",
    "item_kind", "__file_ref",
)


async def _resolve(source, parts, monitor=None):
    return await TraversalResolverStrategy(monitor=monitor).try_resolve(source, parts)


# ══ 1. plain dicts are untouched ═════════════════════════════════════════

class TestPlainDictUnchanged:

    async def test_message_is_the_original(self):
        with pytest.raises(VariableResolutionError) as exc:
            await _resolve({"a": 1, "b": 2}, ("missing",))
        assert str(exc.value) == (
            "Key 'missing' not found in dict. Available keys: ['a', 'b']"
        )

    async def test_a_plain_dict_that_happens_to_have_reference_field_names(self):
        """No ``__file_ref`` marker = not an envelope, whatever the field names."""
        plain = {"file_id": "user-data", "columns": ["x"]}
        assert await _resolve(plain, ("file_id",)) == "user-data"
        with pytest.raises(VariableResolutionError) as exc:
            await _resolve(plain, ("nope",))
        assert "Available keys: ['columns', 'file_id']" in str(exc.value)

    async def test_nested_traversal_and_flat_keys_still_work(self):
        assert await _resolve({"a": {"b": 7}}, ("a", "b")) == 7
        assert await _resolve({"a.b": 9}, ("a", "b")) == 9


# ══ 2. a file_ref describes its DATA ═════════════════════════════════════

class TestFileRefReportsDataFields:

    async def test_it_lists_the_columns_not_the_envelope_keys(self):
        with pytest.raises(VariableResolutionError) as exc:
            await _resolve(_envelope(), ("reqID_typo",))
        msg = str(exc.value)
        for field in ("'status'", "'reqID'", "'raw'", "'error'"):
            assert field in msg, f"{field} missing from: {msg}"
        for leaked in _ENVELOPE_ONLY:
            assert leaked not in msg, f"envelope field {leaked!r} leaked into: {msg}"

    async def test_the_synthetic_row_id_column_is_not_offered_as_a_field(self):
        with pytest.raises(VariableResolutionError) as exc:
            await _resolve(_envelope(), ("nope",))
        assert ROW_ID_COLUMN not in str(exc.value)

    async def test_it_names_the_file_so_the_reader_can_find_it(self):
        with pytest.raises(VariableResolutionError) as exc:
            await _resolve(_envelope(), ("nope",))
        assert "F-123" in str(exc.value)


# ══ 3. the live case: the upstream node produced nothing ═════════════════

class TestEmptyUpstreamOutputSaysSo:

    @pytest.mark.parametrize("columns", [
        [ROW_ID_COLUMN],        # tenant-1 run 18847bb8
        [],
        None,
    ])
    async def test_no_data_fields_is_reported_as_an_empty_upstream_value(self, columns):
        env = _envelope(columns=columns, row_count=1, size_bytes=11)
        with pytest.raises(VariableResolutionError) as exc:
            await _resolve(env, ("reqID",))
        msg = str(exc.value).lower()
        assert "empty" in msg, msg
        assert "no data fields" in msg, msg

    async def test_the_exact_tenant_1_failure_now_reads_correctly(self):
        """Reproduces run 18847bb8's envelope verbatim."""
        env = _envelope(columns=[ROW_ID_COLUMN], row_count=1, size_bytes=11)
        with pytest.raises(VariableResolutionError) as exc:
            await _resolve(env, ("reqID",))
        msg = str(exc.value)
        # The old message sent the reader into the storage layer.
        assert "arrow_file_id" not in msg
        assert "etag" not in msg
        # The new one points at the node that produced nothing.
        assert "returned an empty value" in msg


# ══ 4. bounded as the data grows ═════════════════════════════════════════

class TestItStaysBoundedAsColumnsGrow:
    """A wide CSV must not turn one failed expression into a multi-KB exception
    that is then stored on the run, echoed to the FE and written to the log."""

    @pytest.mark.parametrize("n_cols", [1, 10, 400, 2000])
    async def test_message_length_does_not_track_column_count(self, n_cols):
        env = _envelope(columns=[ROW_ID_COLUMN] + [f"f{i}" for i in range(n_cols)])
        with pytest.raises(VariableResolutionError) as exc:
            await _resolve(env, ("nope",))
        msg = str(exc.value)
        assert len(msg) < 1200, f"{n_cols} columns produced a {len(msg)}-char message"

    @pytest.mark.parametrize("n_cols", [400, 2000])
    async def test_truncation_is_stated_not_silent(self, n_cols):
        env = _envelope(columns=[ROW_ID_COLUMN] + [f"f{i}" for i in range(n_cols)])
        with pytest.raises(VariableResolutionError) as exc:
            await _resolve(env, ("nope",))
        assert "more" in str(exc.value), "a truncated list must say it was truncated"

    async def test_a_short_list_is_not_truncated(self):
        with pytest.raises(VariableResolutionError) as exc:
            await _resolve(_envelope(), ("nope",))
        assert "more" not in str(exc.value)


# ══ 5. bare reference reads: keep file_id, count the rest (shadow) ═══════

class TestBareEnvelopeReads:

    async def test_file_id_still_resolves(self):
        """39 call sites, incl. the data-edit node's ``{{$input.file_id}}``.
        Breaking this to tidy the namespace is not a trade worth making."""
        assert await _resolve(_envelope(), ("file_id",)) == "F-123"

    async def test_file_id_is_not_flagged(self):
        mon = _Monitor()
        await _resolve(_envelope(), ("file_id",), monitor=mon)
        assert mon.calls == []

    async def test_other_reference_fields_are_still_served_shadow_not_refused(self):
        """SHADOW. Refusing them today would break an unknown customer workflow
        on reasoning alone; the counter is how that decision gets made on
        numbers instead."""
        mon = _Monitor()
        assert await _resolve(_envelope(), ("version",), monitor=mon) == "envelope-v9"
        assert await _resolve(_envelope(), ("row_count",), monitor=mon) == 1

    async def test_other_reference_fields_are_counted(self):
        mon = _Monitor()
        await _resolve(_envelope(), ("version",), monitor=mon)
        await _resolve(_envelope(), ("etag",), monitor=mon)
        assert [m for m, _ in mon.calls] == [
            "variable_resolve_envelope_bare_read"] * 2
        # Same label KEYS on every sample — a mismatched set is dropped silently
        # by the Prometheus client (see the monitor's frozen-label contract).
        assert [sorted(t) for _, t in mon.calls] == [["key"], ["key"]]
        assert [t["key"] for _, t in mon.calls] == ["version", "etag"]

    async def test_the_label_is_the_reference_field_so_cardinality_is_bounded(self):
        """The counter only fires when the key IS a reference field, so the
        label ranges over ~16 fixed values — not over author-chosen names."""
        mon = _Monitor()
        await _resolve(_envelope(), ("version",), monitor=mon)
        assert mon.calls[0][1]["key"] in _envelope()

    async def test_it_warns_so_the_log_shows_which_expression(self, caplog):
        with caplog.at_level(logging.WARNING):
            await _resolve(_envelope(), ("etag",))
        assert any("etag" in r.getMessage() for r in caplog.records), caplog.text
        assert any("__etag" in r.getMessage() for r in caplog.records), (
            "the warning must name the dunder form to migrate to")

    async def test_no_monitor_wired_is_not_an_error(self):
        assert await _resolve(_envelope(), ("version",)) == "envelope-v9"

    async def test_a_broken_monitor_never_breaks_resolution(self):
        class _Boom:
            def increment(self, *a, **k):
                raise RuntimeError("metrics down")
        assert await _resolve(_envelope(), ("version",), monitor=_Boom()) == "envelope-v9"

    async def test_the_flat_key_shortcut_is_covered_too(self):
        """``try_resolve`` answers every SINGLE-segment expression from the
        flat-key shortcut at the top, before the traversal loop runs. A hook
        placed only in the loop misses ``{{$node.version}}`` — the exact case
        the counter exists to measure — and then reads a reassuring zero.
        This test is why that bug did not ship.
        """
        mon = _Monitor()
        await _resolve(_envelope(), ("version",), monitor=mon)   # 1 segment: shortcut
        await _resolve({"n": _envelope()}, ("n", "version"), monitor=mon)  # loop
        assert len(mon.calls) == 2, "one of the two lookup paths is unhooked"

    async def test_nested_file_ref_inside_a_plain_dict_is_still_walked(self):
        """SA-1949 shape: an inline-output node emits ``{"result": <file_ref>}``.
        The outer dict is not an envelope, so walking ``result`` must not flag."""
        mon = _Monitor()
        outer = {"result": _envelope()}
        assert await _resolve(outer, ("result", "file_id"), monitor=mon) == "F-123"
        assert mon.calls == []


# == 6. the module's ASCII-only rule for message strings ==================

class TestMessagesStayAscii:
    """``_one_line``'s docstring states the rule and the reason: these strings
    are formatted while handling an exception, then logged, persisted on the
    task row and put on the wire. A ``UnicodeEncodeError`` raised there (cp1252
    handlers and latin-1 log sinks both do it) replaces a precise diagnostic
    with a crash inside the error path."""

    @pytest.mark.parametrize("columns", [
        None,
        [ROW_ID_COLUMN],
        [ROW_ID_COLUMN, "status", "reqID"],
        [ROW_ID_COLUMN] + [f"f{i}" for i in range(2000)],
    ])
    async def test_every_missing_key_message_encodes_as_ascii(self, columns):
        with pytest.raises(VariableResolutionError) as exc:
            await _resolve(_envelope(columns=columns), ("nope",))
        str(exc.value).encode("ascii")          # raises if a smart dash crept in

    async def test_the_plain_dict_message_too(self):
        with pytest.raises(VariableResolutionError) as exc:
            await _resolve({"a": 1}, ("nope",))
        str(exc.value).encode("ascii")
