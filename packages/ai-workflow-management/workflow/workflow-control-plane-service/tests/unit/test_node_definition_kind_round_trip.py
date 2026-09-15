"""Regression tests for SA-1736 kind/collection round-trip (control-plane).

The bug: a node definition stores ``kind``/``collection`` (SA-1736), but the
GET-single and LIST-items read paths never grew those two fields — only the
create/update use cases and the HANA repo did. So the builder form fetched a
definition, rendered ``kind``/``collection`` blank, and re-posted the blanks
on save. ``UpdateNodeDefinitionUseCase`` treated ``kind=""`` as "set it to
empty string" (``is not None`` guard), and ``NodeDefinition.__post_init__``
then silently healed the blank into a *different* value than what the author
had chosen (``kind="action"``, ``collection="My Nodes"``) -- moving the node
to a different palette band with no warning and no error.

This file is written test-first (TDD): tests 1-4 are RED against the
pre-fix code (GET/LIST omit the fields entirely; the update guard clobbers
`kind` on a blank string) and turn GREEN once the fix lands.
"""

import dataclasses

import pytest

from app.layer2_application.features.create_node_definition.use_cases.create_node_definition_use_case import (
    CreateNodeDefinitionUseCase, CreateNodeDefinitionCommand,
)
from app.layer2_application.features.get_node_definition.use_cases.get_node_definition_use_case import (
    GetNodeDefinitionUseCase, GetNodeDefinitionCommand, GetNodeDefinitionResult,
)
from app.layer2_application.features.list_node_definitions.use_cases.list_node_definitions_use_case import (
    ListNodeDefinitionsUseCase, ListNodeDefinitionsCommand,
)
from app.layer2_application.features.update_node_definition.use_cases.update_node_definition_use_case import (
    UpdateNodeDefinitionUseCase, UpdateNodeDefinitionCommand,
)


@pytest.fixture
def create_uc(logger, node_definition_repo):
    return CreateNodeDefinitionUseCase(logger=logger, node_definition_repo=node_definition_repo)


@pytest.fixture
def get_uc(logger, node_definition_repo):
    return GetNodeDefinitionUseCase(logger=logger, node_definition_repo=node_definition_repo)


@pytest.fixture
def list_uc(logger, node_definition_repo):
    return ListNodeDefinitionsUseCase(logger=logger, node_definition_repo=node_definition_repo)


@pytest.fixture
def update_uc(logger, node_definition_repo):
    return UpdateNodeDefinitionUseCase(logger=logger, node_definition_repo=node_definition_repo)


class TestGetNodeDefinitionCarriesKindAndCollection:

    async def test_get_returns_kind_and_collection(self, create_uc, get_uc):
        """The regression itself: today GetNodeDefinitionResult doesn't even
        have `kind`/`collection` attributes, so the builder form renders both
        blank on every GET -- even though the definition was created with
        real values."""
        created = await create_uc.execute(CreateNodeDefinitionCommand(
            tenant_id="t1", name="Get Balance", base_worker_id="http-worker",
            kind="read", collection="Finance",
        ))
        assert created.kind == "read"
        assert created.collection == "Finance"

        result = await get_uc.execute(GetNodeDefinitionCommand(node_definition_id=created.id))
        assert result.error == ""
        assert result.kind == "read"
        assert result.collection == "Finance"

    def test_get_result_declares_the_fields(self):
        """Guard for the exact defect class: a result object that silently
        omits a field looks fine at every other layer (no exception, no type
        error) -- the only way to catch "the shape is wrong" is to assert the
        shape, not just a value. Mirrors the guard added for SA-1735's HTTP
        DTO."""
        field_names = {f.name for f in dataclasses.fields(GetNodeDefinitionResult)}
        assert "kind" in field_names
        assert "collection" in field_names


class TestListNodeDefinitionsCarriesKindAndCollection:

    async def test_list_items_carry_kind_and_collection(self, create_uc, list_uc):
        await create_uc.execute(CreateNodeDefinitionCommand(
            tenant_id="t1", name="Get Balance", base_worker_id="http-worker",
            kind="read", collection="Finance",
        ))
        result = await list_uc.execute(ListNodeDefinitionsCommand(tenant_id="t1"))
        assert result.total == 1
        item = result.items[0]
        assert "kind" in item
        assert "collection" in item
        assert item["kind"] == "read"
        assert item["collection"] == "Finance"


class TestUpdateNodeDefinitionPreservesKindAndCollection:

    async def test_editing_only_the_description_does_not_destroy_kind_or_collection(
        self, create_uc, update_uc,
    ):
        """THE bug, reproduced exactly as it happened live:

        BEFORE:  kind=read    collection=Finance    -> category = Finance.read
        AFTER:   kind=action  collection=My Nodes   -> category = My Nodes.action

        The builder form GET'd a definition, rendered kind/collection blank
        (because GET didn't return them), the user edited only the
        description, and the FE posted kind="" / collection="" alongside the
        real description change. The update use case's `is not None` guard on
        `kind` let the blank string through, and `NodeDefinition.__post_init__`
        healed "" into "action" -- silently moving the node to a different
        palette band.
        """
        created = await create_uc.execute(CreateNodeDefinitionCommand(
            tenant_id="t1", name="Get Balance", base_worker_id="http-worker",
            kind="read", collection="Finance",
        ))
        assert created.kind == "read"
        assert created.collection == "Finance"

        result = await update_uc.execute(UpdateNodeDefinitionCommand(
            node_definition_id=created.id,
            description="Fetches the current account balance.",
            kind="",  # what the FE sent when its (unfixed) form rendered blank
        ))
        assert result.error == ""
        assert result.description == "Fetches the current account balance."
        assert result.kind == "read", "a blank kind must NEVER overwrite the author's real value"
        assert result.collection == "Finance"

    async def test_a_blank_collection_no_longer_wipes_the_stored_value(self, create_uc, update_uc):
        """FIX3 (data-loss blocker): this test used to assert that
        ``collection=""`` resets the stored collection to "My Nodes" -- that
        was blessing the exact hole this PR closes. The same stale client that
        posts ``kind=""`` also posts ``collection=""``, so treating a blank
        collection as "reset to default" silently destroyed a user's real
        collection (e.g. "Finance") on the very same no-op save that FIX3's
        `kind` guard protects. A blank must never destroy: `collection` is now
        symmetric with `kind` -- a blank means "no value supplied", not "clear
        it". A user who genuinely wants the default collection must post
        "My Nodes" explicitly."""
        created = await create_uc.execute(CreateNodeDefinitionCommand(
            tenant_id="t1", name="Get Balance", base_worker_id="http-worker",
            kind="read", collection="Finance",
        ))
        result = await update_uc.execute(UpdateNodeDefinitionCommand(
            node_definition_id=created.id,
            collection="",
        ))
        assert result.error == ""
        assert result.collection == "Finance", "a blank collection must NEVER overwrite the author's real value"

    async def test_description_only_edit_with_blank_kind_and_collection_both_survive(
        self, create_uc, update_uc,
    ):
        """The missing case from the original regression: a client that sends
        a blank `kind` AND a blank `collection` alongside a real
        description-only edit must leave BOTH taxonomy fields untouched."""
        created = await create_uc.execute(CreateNodeDefinitionCommand(
            tenant_id="t1", name="Get Balance", base_worker_id="http-worker",
            kind="read", collection="Finance",
        ))
        result = await update_uc.execute(UpdateNodeDefinitionCommand(
            node_definition_id=created.id,
            description="Fetches the current account balance.",
            kind="",
            collection="",
        ))
        assert result.error == ""
        assert result.description == "Fetches the current account balance."
        assert result.kind == "read"
        assert result.collection == "Finance"

    async def test_an_explicit_kind_change_still_works(self, create_uc, update_uc):
        """The new `if command.kind:` guard must only block a blank string --
        a real, explicit kind change must still go through."""
        created = await create_uc.execute(CreateNodeDefinitionCommand(
            tenant_id="t1", name="Get Balance", base_worker_id="http-worker",
            kind="read", collection="Finance",
        ))
        result = await update_uc.execute(UpdateNodeDefinitionCommand(
            node_definition_id=created.id,
            kind="util",
        ))
        assert result.error == ""
        assert result.kind == "util"

    async def test_an_explicit_collection_change_still_works(self, create_uc, update_uc):
        """The new `if command.collection:` guard (FIX3) must only block a
        blank string -- a real, explicit collection change must still go
        through, same as `kind`."""
        created = await create_uc.execute(CreateNodeDefinitionCommand(
            tenant_id="t1", name="Get Balance", base_worker_id="http-worker",
            kind="read", collection="Finance",
        ))
        result = await update_uc.execute(UpdateNodeDefinitionCommand(
            node_definition_id=created.id,
            collection="Accounting",
        ))
        assert result.error == ""
        assert result.collection == "Accounting"
