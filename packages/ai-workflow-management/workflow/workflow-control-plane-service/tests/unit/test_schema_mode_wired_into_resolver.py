"""``SCHEMA_REF_MODE`` is wired into the resolver, and `off` really is inert.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §19.

``test_schema_dictionary.py`` proves the lookup and the verifier. This proves
they are *connected* to the one place a descriptor's schema is about to be read
— the distinction this workstream has paid for once already, when the
`DROP CONTEXT` topology fix had nine green tests and no call site.

The mode matters here specifically because the failure it guards is silent: a
ref whose schema cannot be restored does not raise, it makes
``CsvConverter._unflatten`` fall back to ``_try_parse_value`` and **guess** the
types.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.data_reference import build_file_ref, schema_hash
from app.layer2_application.data import schema_dictionary as sd
from app.layer2_application.resolve.file_backed_resolver import (
    FileBackedResolverStrategy,
)

COLS = ["sku", "qty"]
TYPES = {"sku": "string", "qty": "number"}
DIGEST = schema_hash(COLS, TYPES)


def _ref():
    return build_file_ref(file_id="f1", row_count=2, columns=COLS,
                          column_types=TYPES)


def _dictionary(entries=None, spy=None):
    entries = ({DIGEST: {"columns": COLS, "column_types": TYPES}}
               if entries is None else entries)

    def _fetch(digests):
        if spy is not None:
            spy.append(list(digests))
        return {d: entries[d] for d in digests if d in entries}

    return sd.SchemaDictionary(_fetch)


def _strategy(dictionary):
    return FileBackedResolverStrategy(
        data_store_service=None, file_client=None, schema_dictionary=dictionary,
    )


class TestOffTouchesNothing:
    def test_the_dictionary_is_never_consulted(self, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "off")
        spy = []
        _strategy(_dictionary(spy=spy))._apply_schema_mode(_ref())
        assert spy == [], "off must not cost a lookup on the hot resolve path"

    def test_the_ref_is_unchanged(self, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "off")
        ref = _ref()
        before = dict(ref)
        _strategy(_dictionary())._apply_schema_mode(ref)
        assert ref == before


class TestShadowReports:
    def test_a_missing_entry_is_logged(self, monkeypatch, caplog):
        import logging
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "shadow")
        with caplog.at_level(logging.WARNING):
            _strategy(_dictionary({}))._apply_schema_mode(_ref())
        assert "schema-dictionary divergence" in caplog.text
        assert "missing:" in caplog.text
        assert "f1" in caplog.text, "the log must name the file it is about"

    def test_agreement_is_silent(self, monkeypatch, caplog):
        import logging
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "shadow")
        with caplog.at_level(logging.WARNING):
            _strategy(_dictionary())._apply_schema_mode(_ref())
        assert "divergence" not in caplog.text

    def test_shadow_does_NOT_modify_the_ref(self, monkeypatch):
        """Shadow observes. A ref that came back changed would mean the mode
        was already enforcing under another name."""
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "shadow")
        ref = _ref()
        before = dict(ref)
        _strategy(_dictionary())._apply_schema_mode(ref)
        assert ref == before


class TestOnExpands:
    def test_a_stripped_ref_gets_its_schema_back(self, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        thin = sd.strip(_ref())
        assert "columns" not in thin
        _strategy(_dictionary())._apply_schema_mode(thin)
        assert thin["columns"] == COLS and thin["column_types"] == TYPES

    def test_a_miss_leaves_it_thin_rather_than_half_filled(self, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        thin = sd.strip(_ref())
        _strategy(_dictionary({}))._apply_schema_mode(thin)
        assert "columns" not in thin


class TestItCannotBreakAResolve:
    """Every branch here is best-effort. A schema problem must cost accuracy of
    a log line, never a resolved expression."""

    def test_no_dictionary_wired_is_a_no_op(self, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "shadow")
        _strategy(None)._apply_schema_mode(_ref())

    def test_a_raising_dictionary_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "shadow")

        class _Boom:
            def get(self, *_a, **_k):
                raise RuntimeError("hana down")

            def get_many(self, *_a, **_k):
                raise RuntimeError("hana down")

        _strategy(_Boom())._apply_schema_mode(_ref())

    @pytest.mark.parametrize("weird", [None, {}, [], "text", 7])
    def test_odd_sources_do_not_raise(self, monkeypatch, weird):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "shadow")
        _strategy(_dictionary())._apply_schema_mode(weird)


@pytest.mark.asyncio
class TestItIsACTUALLYWIRED:
    """Driven through the real ``try_resolve``, not the helper — an unwired mode
    flag is indistinguishable from no mode flag."""

    async def test_try_resolve_runs_the_shadow_check(self, monkeypatch, caplog):
        import logging
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "shadow")
        strategy = _strategy(_dictionary({}))
        with caplog.at_level(logging.WARNING):
            try:
                await strategy.try_resolve(_ref(), ("sku",))
            except Exception:
                pass  # the resolve itself needs a file client; the check runs first
        assert "schema-dictionary divergence" in caplog.text, (
            "try_resolve must apply the schema mode — this is the call every "
            "file-backed resolution passes through"
        )

    async def test_a_non_file_ref_source_skips_it(self, monkeypatch, caplog):
        import logging
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "shadow")
        with caplog.at_level(logging.WARNING):
            await _strategy(_dictionary({})).try_resolve({"plain": 1}, ("plain",))
        assert "schema-dictionary" not in caplog.text
