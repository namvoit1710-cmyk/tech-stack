"""Unit specs for ``ResolveVariablePathsUseCase`` (layer2 application use-case).

Batched, read-only, per-path-isolated variable evaluator. Collaborators:
  - ``run_repo.aget_by_id`` — async  → AsyncMock
  - ``variable_resolver.resolve`` — async → AsyncMock
  - ``run_context_view_store.get`` — sync → MagicMock
(MOCK_KIND_DRIFT forbidden: async→AsyncMock, sync→MagicMock.) ``execute`` is async.

Gap-fill: the module sat at 49% with only integration coverage (sweep
2026-07-02). Every expected code / kind is grounded in the source of
``resolve_variable_paths_use_case.py`` (MAX_PATHS=100, per-path error codes,
``_classify`` heuristics) + ``ErrorCode``.

Case types: happy · edge · invalid input · boundary · failure path.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer1_domain.entities.data_reference import FILE_REF_MARKER
from app.layer2_application.errors import ErrorCode
from app.layer2_application.features.resolve_variable_paths.use_cases.resolve_variable_paths_use_case import (
    MAX_PATHS,
    ResolveVariablePathsCommand,
    ResolveVariablePathsUseCase,
)
from app.layer2_application.resolve.variable_resolver import VariableResolutionError


def _make_run(run_id="run-1", variables=None):
    ctx = SimpleNamespace(variables=variables or {})
    return SimpleNamespace(id=run_id, context=ctx)


def _make_uc(run=None, resolve_return=None, resolve_side_effect=None, view_store=None):
    run_repo = MagicMock()
    run_repo.aget_by_id = AsyncMock(return_value=run)
    resolver = MagicMock()
    resolver.resolve = AsyncMock(
        return_value=resolve_return, side_effect=resolve_side_effect,
    )
    logger = MagicMock()
    uc = ResolveVariablePathsUseCase(
        logger=logger,
        run_repo=run_repo,
        variable_resolver=resolver,
        run_context_view_store=view_store,
    )
    return uc, run_repo, resolver


# ── boundary: too many paths → BAD_REQUEST, run never fetched ─────────
@pytest.mark.asyncio
async def test_too_many_paths_returns_bad_request():
    uc, run_repo, resolver = _make_uc(run=_make_run())
    paths = [f"$.n.{i}" for i in range(MAX_PATHS + 1)]
    result = await uc.execute(ResolveVariablePathsCommand(run_id="run-1", paths=paths))
    assert result.error_code == ErrorCode.BAD_REQUEST.code
    assert result.error_details == {"limit": MAX_PATHS, "received": MAX_PATHS + 1}
    run_repo.aget_by_id.assert_not_awaited()


# ── boundary: exactly MAX_PATHS is allowed (limit is strict '>') ──────
@pytest.mark.asyncio
async def test_exactly_max_paths_allowed():
    uc, run_repo, resolver = _make_uc(run=_make_run(), resolve_return="v")
    paths = [f"$.n.{i}" for i in range(MAX_PATHS)]
    result = await uc.execute(ResolveVariablePathsCommand(run_id="run-1", paths=paths))
    assert result.error_code == ""
    assert len(result.results) == MAX_PATHS
    run_repo.aget_by_id.assert_awaited_once_with("run-1")


# ── failure path: run not found → RUN_NOT_FOUND ───────────────────────
@pytest.mark.asyncio
async def test_run_not_found():
    uc, run_repo, resolver = _make_uc(run=None)
    result = await uc.execute(ResolveVariablePathsCommand(run_id="missing", paths=["$.a"]))
    assert result.error_code == ErrorCode.RUN_NOT_FOUND.code
    assert result.error_details == {"run_id": "missing"}
    resolver.resolve.assert_not_awaited()


# ── happy: a scalar resolves ok with kind="scalar" ────────────────────
@pytest.mark.asyncio
async def test_happy_scalar_resolution():
    uc, _run_repo, resolver = _make_uc(run=_make_run(), resolve_return=42)
    result = await uc.execute(ResolveVariablePathsCommand(run_id="run-1", paths=["$.n.x"]))
    assert result.error_code == ""
    [pr] = result.results
    assert pr.status == "ok"
    assert pr.kind == "scalar"
    assert pr.value == 42
    resolver.resolve.assert_awaited_once()


# ── happy: a file-ref dict classifies as "artifact" ───────────────────
@pytest.mark.asyncio
async def test_file_ref_classified_as_artifact():
    file_ref = {FILE_REF_MARKER: True, "file_id": "f1"}
    uc, _run_repo, _resolver = _make_uc(run=_make_run(), resolve_return=file_ref)
    result = await uc.execute(ResolveVariablePathsCommand(run_id="run-1", paths=["$.n"]))
    assert result.results[0].kind == "artifact"


# ── edge: a dict with file_id but NO marker classifies as "file_ref" ──
@pytest.mark.asyncio
async def test_file_metadata_classified_as_file_ref():
    meta = {"file_id": "abc", "row_count": 3}  # no __file_ref marker
    uc, _run_repo, _resolver = _make_uc(run=_make_run(), resolve_return=meta)
    result = await uc.execute(ResolveVariablePathsCommand(run_id="run-1", paths=["$.n.__meta"]))
    assert result.results[0].kind == "file_ref"


# ── edge: list of dicts from a [*] path → "artifact_row" ──────────────
@pytest.mark.asyncio
async def test_list_of_dicts_from_iterator_is_artifact_row():
    rows = [{"c": 1}, {"c": 2}]
    uc, _run_repo, _resolver = _make_uc(run=_make_run(), resolve_return=rows)
    result = await uc.execute(
        ResolveVariablePathsCommand(run_id="run-1", paths=["$.n[*]"]),
    )
    assert result.results[0].kind == "artifact_row"


# ── edge: list of primitives from a [*] path → "artifact_column" ──────
@pytest.mark.asyncio
async def test_list_of_primitives_from_iterator_is_artifact_column():
    uc, _run_repo, _resolver = _make_uc(run=_make_run(), resolve_return=[1, 2, 3])
    result = await uc.execute(
        ResolveVariablePathsCommand(run_id="run-1", paths=["$.n[*].c"]),
    )
    assert result.results[0].kind == "artifact_column"


# ── invalid input: empty/blank path → per-path INVALID_PATH, no resolve ─
@pytest.mark.asyncio
async def test_blank_path_yields_invalid_path_error():
    uc, _run_repo, resolver = _make_uc(run=_make_run())
    result = await uc.execute(ResolveVariablePathsCommand(run_id="run-1", paths=["   "]))
    pr = result.results[0]
    assert pr.status == "error"
    assert pr.error["code"] == "INVALID_PATH"
    resolver.resolve.assert_not_awaited()


# ── failure path: resolver raises VariableResolutionError → PATH_NOT_FOUND ─
@pytest.mark.asyncio
async def test_resolution_error_yields_path_not_found():
    uc, _run_repo, _resolver = _make_uc(
        run=_make_run(), resolve_side_effect=VariableResolutionError("no such path"),
    )
    result = await uc.execute(ResolveVariablePathsCommand(run_id="run-1", paths=["$.missing"]))
    pr = result.results[0]
    assert pr.status == "error"
    assert pr.error["code"] == "PATH_NOT_FOUND"
    assert "no such path" in pr.error["message"]


# ── per-path isolation: one bad path does not fail the batch ──────────
@pytest.mark.asyncio
async def test_per_path_isolation_mixed_results():
    async def _resolve(path, ctx):
        if path == "$.bad":
            raise VariableResolutionError("nope")
        return "ok-value"

    run_repo = MagicMock()
    run_repo.aget_by_id = AsyncMock(return_value=_make_run())
    resolver = MagicMock()
    resolver.resolve = AsyncMock(side_effect=_resolve)
    uc = ResolveVariablePathsUseCase(
        logger=MagicMock(), run_repo=run_repo, variable_resolver=resolver,
    )
    result = await uc.execute(
        ResolveVariablePathsCommand(run_id="run-1", paths=["$.good", "$.bad"]),
    )
    assert result.error_code == ""  # batch itself succeeds
    statuses = {r.path: r.status for r in result.results}
    assert statuses == {"$.good": "ok", "$.bad": "error"}


# ── view store merge: projector view value wins over aggregate ────────
@pytest.mark.asyncio
async def test_view_store_merged_into_context():
    # The resolver receives the merged context; assert the merged dict carries
    # both the aggregate key and the view key (view wins on conflict).
    view = SimpleNamespace(variables={"nodeA": "from-view"})
    view_store = MagicMock()
    view_store.get.return_value = view
    run = _make_run(variables={"nodeA": "from-aggregate", "artifacts": {}})
    captured = {}

    async def _resolve(path, ctx):
        captured.update(ctx)
        return "v"

    run_repo = MagicMock()
    run_repo.aget_by_id = AsyncMock(return_value=run)
    resolver = MagicMock()
    resolver.resolve = AsyncMock(side_effect=_resolve)
    uc = ResolveVariablePathsUseCase(
        logger=MagicMock(), run_repo=run_repo, variable_resolver=resolver,
        run_context_view_store=view_store,
    )
    await uc.execute(ResolveVariablePathsCommand(run_id="run-1", paths=["$.nodeA"]))
    assert captured["nodeA"] == "from-view"       # view overrides aggregate
    assert "artifacts" in captured                 # aggregate fills the gap
    view_store.get.assert_called_once_with("run-1")


# ── view store failure is swallowed → falls back to aggregate ─────────
@pytest.mark.asyncio
async def test_view_store_exception_falls_back_to_aggregate():
    view_store = MagicMock()
    view_store.get.side_effect = RuntimeError("view down")
    run = _make_run(variables={"k": "agg"})
    captured = {}

    async def _resolve(path, ctx):
        captured.update(ctx)
        return "v"

    run_repo = MagicMock()
    run_repo.aget_by_id = AsyncMock(return_value=run)
    resolver = MagicMock()
    resolver.resolve = AsyncMock(side_effect=_resolve)
    uc = ResolveVariablePathsUseCase(
        logger=MagicMock(), run_repo=run_repo, variable_resolver=resolver,
        run_context_view_store=view_store,
    )
    result = await uc.execute(ResolveVariablePathsCommand(run_id="run-1", paths=["$.k"]))
    assert result.results[0].status == "ok"
    assert captured["k"] == "agg"
