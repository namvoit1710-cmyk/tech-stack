"""``id`` is the handle that cannot fail; ``name`` is the one that did.

On tenant-1 (2026-08-12) 42 gateway apps were unreachable by every verb because
their name cannot occupy a URL path segment. ``resource_name`` stops new ones
being written, but a rule added today does nothing for a row written yesterday,
and nothing for one that arrives by migration or by hand. ``DELETE
/gateway/apps/by-id/{id}`` is the way out that does not need a DBA: the id is a
UUID, always survives a URL, and is already carried in every list response.

Also pinned here: creating an app whose name belongs to a *deleted* app used to
sail past the duplicate check and die at ``INSERT`` on the unique index as a
500. ``IDX_AIW_GW_APPS_NAME`` is unique across all rows including inactive
ones, so the honest answer is a 409 that says the name is reserved.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.layer1_domain.entities.gateway_app import GatewayApp
from app.layer1_domain.value_objects.auth_type import AuthType
from app.layer1_domain.value_objects.gateway import AppKind
from app.layer2_application.features.create_gateway_app.use_cases.create_gateway_app_use_case import (
    CreateGatewayAppCommand,
    CreateGatewayAppUseCase,
)
from app.layer2_application.features.delete_gateway_app.use_cases.delete_gateway_app_use_case import (
    DeleteGatewayAppCommand,
    DeleteGatewayAppUseCase,
)
from app.layer2_application.repositories.gateway_function_repo_interface import (
    ListGatewayFunctionsPage,
)

GHOST_NAME = "WEB-INF/web.xml"   # verbatim from tenant-1; no route can match it


def _row(*, id: str, name: str, is_active: bool = True) -> GatewayApp:
    """Build straight from storage, bypassing ``create``.

    A ghost row cannot go through ``create`` any more — which is the point of
    the fix, and the reason this helper exists.
    """
    return GatewayApp(
        id=id, name=name, kind=AppKind.INTERNAL, base_url="John Doe",
        is_active=is_active, created_by="system", updated_by="system",
    )


class _Repo:
    def __init__(self, rows: list[GatewayApp]) -> None:
        self.rows = {r.id: r for r in rows}
        self.soft_deleted: list[str] = []

    def get_by_id(self, id: str):
        return self.rows.get(id)

    def get_by_name(self, name: str):
        return next((r for r in self.rows.values() if r.name == name), None)

    def soft_delete(self, id: str) -> None:
        self.soft_deleted.append(id)
        self.rows[id].is_active = False

    def save(self, entity, *, expected_version=None) -> None:
        self.rows[entity.id] = entity


def _uc(repo: _Repo, fn_repo=None) -> DeleteGatewayAppUseCase:
    return DeleteGatewayAppUseCase(MagicMock(), repo, fn_repo)


class TestDeleteById:
    @pytest.mark.asyncio
    async def test_a_row_no_route_can_address_is_deletable_by_id(self):
        repo = _Repo([_row(id="ghost-1", name=GHOST_NAME)])
        result = await _uc(repo).execute(
            DeleteGatewayAppCommand(id="ghost-1", deleted_by="janitor")
        )
        assert result.deleted is True and result.error == ""
        assert repo.soft_deleted == ["ghost-1"]

    @pytest.mark.asyncio
    async def test_id_wins_when_both_are_supplied(self):
        """The name on a ghost row is unusable as a lookup key — if the use
        case preferred it, the by-id route would inherit the bug it exists to
        route around."""
        repo = _Repo([
            _row(id="ghost-1", name=GHOST_NAME),
            _row(id="real-1", name="workflow-api"),
        ])
        await _uc(repo).execute(
            DeleteGatewayAppCommand(
                id="ghost-1", name="workflow-api", deleted_by="janitor"
            )
        )
        assert repo.soft_deleted == ["ghost-1"], "must not touch the real app"

    @pytest.mark.asyncio
    async def test_deleting_by_id_twice_is_idempotent(self):
        """The caller found this row in an include_inactive listing and can
        see it. Answering NOT_FOUND about a row on their screen is exactly the
        confusion this whole change is unwinding."""
        repo = _Repo([_row(id="ghost-1", name=GHOST_NAME, is_active=False)])
        result = await _uc(repo).execute(DeleteGatewayAppCommand(id="ghost-1"))
        assert result.deleted is True and result.error == ""
        assert repo.soft_deleted == [], "already retired — must not write again"

    @pytest.mark.asyncio
    async def test_an_unknown_id_is_not_found(self):
        repo = _Repo([])
        result = await _uc(repo).execute(DeleteGatewayAppCommand(id="nope"))
        assert result.deleted is False
        assert result.error_details == {"id": "nope"}

    @pytest.mark.asyncio
    async def test_it_still_cascades_to_the_functions(self):
        fn_repo = MagicMock()
        fn_repo.list_by_app.return_value = ListGatewayFunctionsPage(
            items=[MagicMock(id="fn-1"), MagicMock(id="fn-2")], total=2,
        )
        repo = _Repo([_row(id="ghost-1", name=GHOST_NAME)])
        await _uc(repo, fn_repo).execute(DeleteGatewayAppCommand(id="ghost-1"))
        assert [c.args[0] for c in fn_repo.soft_delete.call_args_list] == ["fn-1", "fn-2"]


class TestDeleteByNameIsUnchanged:
    @pytest.mark.asyncio
    async def test_the_everyday_path_still_works(self):
        repo = _Repo([_row(id="real-1", name="workflow-api")])
        result = await _uc(repo).execute(
            DeleteGatewayAppCommand(name="workflow-api", deleted_by="edgar")
        )
        assert result.deleted is True and repo.soft_deleted == ["real-1"]

    @pytest.mark.asyncio
    async def test_an_inactive_app_is_still_not_found_by_name(self):
        """By name the caller came from a list that hides inactive rows, so
        NOT_FOUND is the truthful answer. Only the by-id path reports state."""
        repo = _Repo([_row(id="real-1", name="workflow-api", is_active=False)])
        result = await _uc(repo).execute(DeleteGatewayAppCommand(name="workflow-api"))
        assert result.deleted is False
        assert result.error_code

    @pytest.mark.asyncio
    async def test_neither_handle_is_a_validation_error(self):
        result = await _uc(_Repo([])).execute(DeleteGatewayAppCommand())
        assert result.error_details == {"field": "name"}


class TestReusingADeletedName:
    @staticmethod
    def _create_uc(repo):
        cipher = MagicMock()
        return CreateGatewayAppUseCase(MagicMock(), repo, cipher)

    @pytest.mark.asyncio
    async def test_a_soft_deleted_name_is_refused_not_500(self):
        """Before: ``existing.is_active`` was False so the check passed, and
        ``repo.save`` INSERTed a second row into a unique index — an unhandled
        DB error, i.e. a 500 on a foreseeable user action."""
        repo = _Repo([_row(id="old-1", name="workflow-api", is_active=False)])
        result = await self._create_uc(repo).execute(CreateGatewayAppCommand(
            name="workflow-api", kind="internal",
            base_url="https://example.com", created_by="edgar",
        ))
        assert result.id == "", "must not have been created"
        assert result.error_code
        assert "reserved" in result.error
        assert result.error_details["existing_is_active"] is False
        assert result.error_details["existing_id"] == "old-1"

    @pytest.mark.asyncio
    async def test_an_active_name_still_reports_plain_name_taken(self):
        repo = _Repo([_row(id="real-1", name="workflow-api")])
        result = await self._create_uc(repo).execute(CreateGatewayAppCommand(
            name="workflow-api", kind="internal",
            base_url="https://example.com", created_by="edgar",
        ))
        assert result.id == ""
        assert result.error_details["existing_is_active"] is True

    @pytest.mark.asyncio
    async def test_a_free_name_is_still_created(self):
        repo = _Repo([])
        result = await self._create_uc(repo).execute(CreateGatewayAppCommand(
            name="billing-v2", kind="internal",
            base_url="https://example.com", created_by="edgar",
        ))
        assert result.error == "" and result.id

    @pytest.mark.asyncio
    async def test_a_scanner_payload_is_refused_before_it_reaches_the_table(self):
        """The end-to-end point of the whole change: the 60 rows that landed on
        tenant-1 came through this use case."""
        repo = _Repo([])
        result = await self._create_uc(repo).execute(CreateGatewayAppCommand(
            name=GHOST_NAME, kind="internal",
            base_url="John Doe", created_by="system",
        ))
        assert result.id == ""
        assert "addressable" in result.error
        assert repo.rows == {}, "nothing may be written"
