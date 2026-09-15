"""The schema dictionary — lookup, verification, and the size claim.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §19.

``schema_hash`` is already on every ref and ``AIW_SCHEMA_DEF`` already holds the
pair it names, so 90.4% of every ``file_ref`` byte on the wire is a lookup key's
worth of data carried in full. These pin the mechanics of not carrying it.

The danger being guarded is not "the ref is wrong" — it is that a missing schema
does **not** raise: ``CsvConverter._unflatten`` falls back to a type-guessing
heuristic. So the tests below care most about the paths where the dictionary
cannot answer.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.data_reference import build_file_ref, schema_hash
from app.layer2_application.data.schema_dictionary import (
    SCHEMA_REF_MODE,
    SchemaDictionary,
    digests_in,
    enforcing,
    expand,
    observing,
    strip,
    verify,
)

COLS = ["sku", "qty"]
TYPES = {"sku": "string", "qty": "number"}
DIGEST = schema_hash(COLS, TYPES)


def _ref(**kw):
    return build_file_ref(file_id="f1", row_count=2, columns=COLS,
                          column_types=TYPES, **kw)


def _dict(entries=None, spy=None):
    entries = {DIGEST: {"columns": COLS, "column_types": TYPES}} if entries is None else entries

    def _fetch(digests):
        if spy is not None:
            spy.append(list(digests))
        return {d: entries[d] for d in digests if d in entries}

    return SchemaDictionary(_fetch)


class TestItShipsInert:
    def test_the_default_mode_is_off(self):
        assert SCHEMA_REF_MODE == "off"
        assert not enforcing() and not observing()


class TestTheCacheCannotGoStale:
    """Content-addressed: a different schema is a different key, so a hit is
    valid forever. That is a property of the data, not an assumption."""

    def test_a_hit_is_fetched_once(self):
        spy = []
        d = _dict(spy=spy)
        assert d.get(DIGEST)["columns"] == COLS
        assert d.get(DIGEST)["columns"] == COLS
        assert len(spy) == 1, "a second lookup must not hit the database"

    def test_a_batch_only_asks_for_what_it_lacks(self):
        spy = []
        other = schema_hash(["a"], {"a": "string"})
        d = _dict({DIGEST: {"columns": COLS, "column_types": TYPES},
                   other: {"columns": ["a"], "column_types": {"a": "string"}}}, spy=spy)
        d.get(DIGEST)
        d.get_many([DIGEST, other])
        assert spy == [[DIGEST], [other]]

    def test_a_miss_is_NOT_cached_as_absent(self):
        """The mirror writes the schema *after* the event is published, so a
        lookup can legitimately race ahead of the writer. Caching 'absent'
        would turn that race into a permanent hole."""
        spy = []
        d = _dict({}, spy=spy)
        assert d.get(DIGEST) is None
        assert d.get(DIGEST) is None
        assert len(spy) == 2, "a miss must be retried, not remembered"
        assert DIGEST in d.misses

    def test_a_dictionary_outage_degrades_to_a_miss(self):
        """A read must fall back to the inline copy, never fail the resolve."""
        def _boom(_digests):
            raise RuntimeError("hana down")

        assert SchemaDictionary(_boom).get(DIGEST) is None


class TestVerifyIsTheGateForFlippingOn:
    def test_a_matching_entry_reports_nothing(self):
        assert verify(_ref(), _dict()) == []

    def test_a_missing_entry_is_reported(self):
        problems = verify(_ref(), _dict({}))
        assert len(problems) == 1 and problems[0].startswith("missing:")

    def test_a_differing_entry_is_reported(self):
        d = _dict({DIGEST: {"columns": ["sku"], "column_types": {"sku": "string"}}})
        problems = verify(_ref(), d)
        assert any(p.startswith("differs:") for p in problems)

    def test_an_entry_that_does_not_hash_to_its_key_is_reported(self):
        """The self-check content addressing buys: recompute the hash from what
        came back. Only fires if ``schema_hash``'s canonicalisation drifted —
        the silent dedup regression its own docstring warns about."""
        d = _dict({DIGEST: {"columns": ["different"], "column_types": {}}})
        assert any(p.startswith("not-content-addressed:") for p in verify(_ref(), d))

    def test_it_walks_the_whole_artifact_tree(self):
        """A descriptor is a tree; one bad child is enough to make ``on`` unsafe.
        Measured: 3,975 refs across 471 events — ~8.4 per event."""
        child_cols = ["id", "line"]
        child = build_file_ref(file_id="f2", columns=child_cols,
                               column_types={"id": "string", "line": "string"})
        parent = _ref(artifacts=[child])
        assert len(digests_in(parent)) == 2
        problems = verify(parent, _dict())  # only the ROOT schema is stored
        assert len(problems) == 1 and problems[0].startswith("missing:")

    def test_a_ref_with_no_schema_is_not_a_problem(self):
        """An empty value has no columns, so ``schema_hash`` is "" and there is
        nothing to look up. Reporting it would drown the real misses."""
        assert verify(build_file_ref(file_id="f1"), _dict({})) == []


class TestExpand:
    def test_it_fills_a_stripped_ref(self):
        thin = strip(_ref())
        assert "columns" not in thin
        expand(thin, _dict())
        assert thin["columns"] == COLS and thin["column_types"] == TYPES

    def test_it_fills_children_too(self):
        child = build_file_ref(file_id="f2", columns=["a"],
                               column_types={"a": "string"})
        thin = strip(_ref(artifacts=[child]))
        d = _dict({DIGEST: {"columns": COLS, "column_types": TYPES},
                   schema_hash(["a"], {"a": "string"}):
                       {"columns": ["a"], "column_types": {"a": "string"}}})
        expand(thin, d)
        assert thin["columns"] == COLS
        assert thin["artifacts"][0]["columns"] == ["a"]

    def test_an_inline_ref_is_left_alone(self):
        """Both shapes ride the wire at once during the transition, and old
        events are NEVER rewritten — so a reader handles both permanently."""
        ref = _ref()
        expand(ref, _dict({}))          # dictionary empty on purpose
        assert ref["columns"] == COLS

    def test_a_miss_leaves_the_ref_untouched_rather_than_half_filled(self):
        thin = strip(_ref())
        expand(thin, _dict({}))
        assert "columns" not in thin, (
            "a half-expanded ref would look schema-less to _unflatten, which "
            "then GUESSES types instead of failing"
        )

    def test_one_batched_fetch_for_a_whole_tree(self):
        child = build_file_ref(file_id="f2", columns=["a"],
                               column_types={"a": "string"})
        spy = []
        d = _dict({DIGEST: {"columns": COLS, "column_types": TYPES},
                   schema_hash(["a"], {"a": "string"}):
                       {"columns": ["a"], "column_types": {"a": "string"}}}, spy=spy)
        expand(strip(_ref(artifacts=[child])), d)
        assert len(spy) == 1, "N artifacts must not cost N queries"


class TestStripIsTheSizeClaim:
    def test_the_hash_survives_and_the_schema_does_not(self):
        thin = strip(_ref())
        assert thin["schema_hash"] == DIGEST
        assert "columns" not in thin and "column_types" not in thin
        assert thin["file_id"] == "f1"

    def test_it_does_not_mutate_the_original(self):
        ref = _ref()
        strip(ref)
        assert ref["columns"] == COLS

    def test_a_ref_without_a_hash_keeps_its_schema(self):
        """Nothing could restore it. Declining to shrink beats shrinking into an
        unreadable ref — the same decline-over-wrong-data rule as elsewhere."""
        ref = _ref()
        ref["schema_hash"] = ""
        assert strip(ref)["columns"] == COLS

    def test_the_saving_is_the_measured_shape(self):
        """§17.8: columns+column_types are 90.4% of a real ref's bytes. This
        fixture is small, so it only asserts the direction and a floor."""
        import json
        wide = [f"col_{i}" for i in range(120)]
        ref = build_file_ref(file_id="f1", columns=wide,
                             column_types={c: "string" for c in wide})
        before = len(json.dumps(ref))
        after = len(json.dumps(strip(ref)))
        assert after < before * 0.15, f"{before} -> {after}"


class TestModeHelpers:
    @pytest.mark.parametrize("mode,on,shadow", [
        ("off", False, False), ("shadow", False, True), ("on", True, False),
        ("ON", True, False), (" shadow ", False, True), ("typo", False, False),
    ])
    def test_modes(self, monkeypatch, mode, on, shadow):
        import app.layer2_application.data.schema_dictionary as sd
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", mode)
        assert sd.enforcing() is on and sd.observing() is shadow
