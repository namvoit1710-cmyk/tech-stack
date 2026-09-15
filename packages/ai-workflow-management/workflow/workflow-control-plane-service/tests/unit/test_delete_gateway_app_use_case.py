"""Unit specs for ``DeleteGatewayAppUseCase`` (layer2 application use-case).

Soft-deletes a gateway app and cascades a best-effort soft-delete to its
functions. Collaborators are the sync ``IGatewayAppRepo`` / ``IGatewayFunctionRepo``
protocols and ``ILogger`` — all sync, so ``MagicMock`` (MOCK_KIND_DRIFT: never
``AsyncMock`` on a sync method). The use-case's ``execute`` is ``async``.

Gap-fill: the module sat at 55% with no existing unit test (sweep 2026-07-02).
Every expected error code / return is grounded in the source of
``delete_gateway_app_use_case.py`` + ``ErrorCode``.

Case types: happy · edge · invalid input · boundary · failure path.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.layer2_application.errors import ErrorCode
from app.layer2_application.features.delete_gateway_app.use_cases.delete_gateway_app_use_case import (
    DeleteGatewayAppCommand,
    DeleteGatewayAppUseCase,
)
from app.layer2_application.repositories.gateway_function_repo_interface import (
    ListGatewayFunctionsPage,
)


def _make_app(id_="app-1", name="Orders", is_active=True):
    # A light stand-in for GatewayApp — the use-case only reads id/name/is_active.
    return SimpleNamespace(id=id_, name=name, is_active=is_active)


def _make_uc(app_repo=None, function_repo=None):
    logger = MagicMock()
    app_repo = app_repo or MagicMock()
    return (
        DeleteGatewayAppUseCase(
            logger=logger,
            gateway_app_repo=app_repo,
            gateway_function_repo=function_repo,
        ),
        logger,
        app_repo,
    )


# ── invalid input: blank name → VALIDATION_ERROR, repo never touched ──
@pytest.mark.asyncio
async def test_blank_name_returns_validation_error():
    uc, _logger, app_repo = _make_uc()
    result = await uc.execute(DeleteGatewayAppCommand(name="   "))
    assert result.deleted is False
    assert result.error_code == ErrorCode.VALIDATION_ERROR.code
    assert result.error == ErrorCode.VALIDATION_ERROR.default_message
    assert result.error_details == {"field": "name"}
    app_repo.get_by_name.assert_not_called()


# ── failure path: unknown name → GATEWAY_APP_NOT_FOUND ────────────────
@pytest.mark.asyncio
async def test_unknown_app_returns_not_found():
    app_repo = MagicMock()
    app_repo.get_by_name.return_value = None
    uc, _logger, app_repo = _make_uc(app_repo=app_repo)
    result = await uc.execute(DeleteGatewayAppCommand(name="Nope"))
    assert result.deleted is False
    assert result.error_code == ErrorCode.GATEWAY_APP_NOT_FOUND.code
    assert result.error_details == {"name": "Nope"}
    # name is stripped before lookup (source: command.name.strip()).
    app_repo.get_by_name.assert_called_once_with("Nope")


# ── edge: already-inactive app is treated as not-found ────────────────
@pytest.mark.asyncio
async def test_inactive_app_treated_as_not_found():
    app_repo = MagicMock()
    app_repo.get_by_name.return_value = _make_app(is_active=False)
    uc, _logger, app_repo = _make_uc(app_repo=app_repo)
    result = await uc.execute(DeleteGatewayAppCommand(name="Orders"))
    assert result.deleted is False
    assert result.error_code == ErrorCode.GATEWAY_APP_NOT_FOUND.code
    app_repo.soft_delete.assert_not_called()


# ── happy: active app, no function repo → soft-delete + cascade skipped ─
@pytest.mark.asyncio
async def test_happy_delete_without_function_repo():
    entity = _make_app(id_="app-9", name="Orders")
    app_repo = MagicMock()
    app_repo.get_by_name.return_value = entity
    uc, logger, app_repo = _make_uc(app_repo=app_repo, function_repo=None)
    result = await uc.execute(DeleteGatewayAppCommand(name=" Orders ", deleted_by="laidon"))
    assert result.deleted is True
    assert result.error == ""
    app_repo.soft_delete.assert_called_once_with("app-9")
    logger.info.assert_called()  # "Gateway app soft-deleted"


# ── happy: cascade soft-deletes each function of the app ──────────────
@pytest.mark.asyncio
async def test_cascade_soft_deletes_functions():
    entity = _make_app(id_="app-2", name="Orders")
    app_repo = MagicMock()
    app_repo.get_by_name.return_value = entity
    fn_repo = MagicMock()
    fn_repo.list_by_app.return_value = ListGatewayFunctionsPage(
        items=[SimpleNamespace(id="fn-1"), SimpleNamespace(id="fn-2")], total=2,
    )
    uc, _logger, app_repo = _make_uc(app_repo=app_repo, function_repo=fn_repo)
    result = await uc.execute(DeleteGatewayAppCommand(name="Orders", deleted_by="laidon"))
    assert result.deleted is True
    # each function soft-deleted, then the app itself.
    assert [c.args[0] for c in fn_repo.soft_delete.call_args_list] == ["fn-1", "fn-2"]
    app_repo.soft_delete.assert_called_once_with("app-2")
    # cascade uses the app's id + include_inactive=False (source filter).
    filt = fn_repo.list_by_app.call_args.args[0]
    assert filt.app_id == "app-2"
    assert filt.include_inactive is False


# ── failure path: a function soft-delete raises → cascade continues ───
@pytest.mark.asyncio
async def test_cascade_survives_one_function_failure():
    entity = _make_app(id_="app-3", name="Orders")
    app_repo = MagicMock()
    app_repo.get_by_name.return_value = entity
    fn_repo = MagicMock()
    fn_repo.list_by_app.return_value = ListGatewayFunctionsPage(
        items=[SimpleNamespace(id="bad"), SimpleNamespace(id="good")], total=2,
    )
    # first call raises, second succeeds (best-effort cascade).
    fn_repo.soft_delete.side_effect = [RuntimeError("boom"), None]
    uc, logger, app_repo = _make_uc(app_repo=app_repo, function_repo=fn_repo)
    result = await uc.execute(DeleteGatewayAppCommand(name="Orders"))
    # the app is still deleted despite the function failure.
    assert result.deleted is True
    app_repo.soft_delete.assert_called_once_with("app-3")
    # the failure is logged as a warning (source: logger.warning in except).
    logger.warning.assert_called()


# ── boundary: empty function page → no cascade info log, still deletes ─
@pytest.mark.asyncio
async def test_empty_function_page_no_cascade_still_deletes():
    entity = _make_app(id_="app-4", name="Orders")
    app_repo = MagicMock()
    app_repo.get_by_name.return_value = entity
    fn_repo = MagicMock()
    fn_repo.list_by_app.return_value = ListGatewayFunctionsPage(items=[], total=0)
    uc, _logger, app_repo = _make_uc(app_repo=app_repo, function_repo=fn_repo)
    result = await uc.execute(DeleteGatewayAppCommand(name="Orders"))
    assert result.deleted is True
    fn_repo.soft_delete.assert_not_called()
    app_repo.soft_delete.assert_called_once_with("app-4")
