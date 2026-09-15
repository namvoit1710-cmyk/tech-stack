"""A thin ref becomes whole at the view boundary, not at the resolver.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §19.3.

``SCHEMA_REF_MODE=on`` strips ``columns``/``column_types`` from refs on the wire,
where they are **90.4%** of every descriptor's bytes. The obvious place to put
them back is the resolver — and it is wrong. Expanding inside
``FileBackedResolverStrategy`` only reaches refs that pass through it, while
19 sites across six modules read a ref's columns without going near it:
``data_store_service`` (8), ``edit_node_data`` (3), ``node_data_projection`` (2),
``data_edit_handler`` (2), ``schema_artifact_provisioner`` (2),
``variable_resolver`` (2).

So it happens here, where ``variables`` first becomes visible to all of them —
the same job this store already does for ``topology``, which the pure reducer
(``apply(envelope)``, one argument, no I/O) equally cannot fetch.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.data_reference import build_file_ref, schema_hash
from app.layer1_domain.run_projection import ProjectedRunState
from app.layer2_application.data import schema_dictionary as sd
from app.layer4_frameworks.run_context.cached_projector_view_store import (
    CachedProjectorRunContextViewStore,
)

COLS = ["sku", "qty"]
TYPES = {"sku": "string", "qty": "number"}
DIGEST = schema_hash(COLS, TYPES)


class _EmptyEventLog:
    """A run with no new events — the warm-cache path, which is the one every
    read actually takes."""

    def get_events(self, *_a, **_k):
        return []

    def list_by_run_since(self, *_a, **_k):
        return []

    def list_by_run(self, *_a, **_k):
        return []

    def append(self, *_a, **_k):
        return None


def _dictionary(entries=None, spy=None):
    entries = ({DIGEST: {"columns": COLS, "column_types": TYPES}}
               if entries is None else entries)

    def _fetch(digests):
        if spy is not None:
            spy.append(list(digests))
        return {d: entries[d] for d in digests if d in entries}

    return sd.SchemaDictionary(_fetch)


def _store(dictionary=None):
    return CachedProjectorRunContextViewStore(
        _EmptyEventLog(), schema_dictionary=dictionary,
    )


def _thin_state():
    thin = sd.strip(build_file_ref(file_id="f1", row_count=2, columns=COLS,
                                   column_types=TYPES))
    assert "columns" not in thin
    return ProjectedRunState(variables={"node-a": thin}), thin


class TestOnPutsTheSchemaBack:
    def test_a_thin_ref_is_whole_by_the_time_the_view_exists(self, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        state, thin = _thin_state()
        _store(_dictionary())._view(state)
        assert thin["columns"] == COLS and thin["column_types"] == TYPES

    def test_a_miss_leaves_it_thin_rather_than_half_filled(self, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        state, thin = _thin_state()
        _store(_dictionary({}))._view(state)
        assert "columns" not in thin

    def test_an_inline_ref_is_untouched(self, monkeypatch):
        """Old events are never rewritten, so complete refs ride the log
        forever alongside thin ones."""
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        fat = build_file_ref(file_id="f1", columns=COLS, column_types=TYPES)
        _store(_dictionary({}))._view(ProjectedRunState(variables={"a": fat}))
        assert fat["columns"] == COLS

    def test_non_ref_variables_are_ignored(self, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        state = ProjectedRunState(variables={"a": {"plain": 1}, "b": 7, "c": None})
        _store(_dictionary())._view(state)
        assert state.variables["a"] == {"plain": 1}


class TestOffAndShadowDoNotTouchTheView:
    @pytest.mark.parametrize("mode", ["off", "shadow"])
    def test_no_lookup_and_no_change(self, monkeypatch, mode):
        """`shadow` reports from the RESOLVER; the view boundary only acts under
        `on`. Expanding in shadow would make the two modes indistinguishable."""
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", mode)
        spy = []
        state, thin = _thin_state()
        _store(_dictionary(spy=spy))._view(state)
        assert spy == []
        assert "columns" not in thin


class TestItCannotBreakAView:
    def test_no_dictionary_wired(self, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        state, _thin = _thin_state()
        assert _store(None)._view(state) is not None

    def test_a_raising_dictionary_still_returns_a_view(self, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")

        class _Boom:
            def get(self, *_a, **_k):
                raise RuntimeError("hana down")

            def get_many(self, *_a, **_k):
                raise RuntimeError("hana down")

        state, _thin = _thin_state()
        assert _store(_Boom())._view(state) is not None

    def test_a_state_without_variables_is_fine(self, monkeypatch):
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        assert _store(_dictionary())._view(ProjectedRunState()) is not None


class TestEveryViewGoesThroughTheOnePlace:
    """Four call sites construct a view. Expanding at three of them and missing
    the fourth is the shape of failure this codebase keeps producing, so the
    construction is funnelled instead of duplicated."""

    def test_the_module_builds_ProjectedView_exactly_once(self):
        import inspect
        from app.layer4_frameworks.run_context import cached_projector_view_store as mod

        src = inspect.getsource(mod)
        built = src.count("ProjectedView(state, workflow_repo=self._workflow_repo)")
        assert built == 1, (
            f"{built} direct ProjectedView constructions — every one must route "
            f"through _view(), or a thin ref reaches a reader unexpanded"
        )

    def test_get_returns_through_the_helper(self, monkeypatch):
        """Behavioural, not textual: drive ``get`` and prove the expansion ran."""
        monkeypatch.setattr(sd, "SCHEMA_REF_MODE", "on")
        store = _store(_dictionary())
        state, thin = _thin_state()
        store._cache["r1"] = (state, 0)
        store._lru["r1"] = None
        store.get("r1")
        assert thin["columns"] == COLS, (
            "get() must expand — it is the call every read path makes"
        )
