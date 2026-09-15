"""Tests for NodeDefinition CRUD use cases."""

import pytest
from app.layer2_application.features.create_node_definition.use_cases.create_node_definition_use_case import (
    CreateNodeDefinitionUseCase, CreateNodeDefinitionCommand,
)
from app.layer2_application.features.get_node_definition.use_cases.get_node_definition_use_case import (
    GetNodeDefinitionUseCase, GetNodeDefinitionCommand,
)
from app.layer2_application.features.list_node_definitions.use_cases.list_node_definitions_use_case import (
    ListNodeDefinitionsUseCase, ListNodeDefinitionsCommand,
)
from app.layer2_application.features.update_node_definition.use_cases.update_node_definition_use_case import (
    UpdateNodeDefinitionUseCase, UpdateNodeDefinitionCommand,
)
from app.layer2_application.features.delete_node_definition.use_cases.delete_node_definition_use_case import (
    DeleteNodeDefinitionUseCase, DeleteNodeDefinitionCommand,
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


@pytest.fixture
def delete_uc(logger, node_definition_repo):
    return DeleteNodeDefinitionUseCase(logger=logger, node_definition_repo=node_definition_repo)


class TestCreateNodeDefinition:

    async def test_create_happy_path(self, create_uc):
        result = await create_uc.execute(CreateNodeDefinitionCommand(
            tenant_id="t1",
            name="Get Orders",
            base_type="TASK",
            base_worker_id="http-worker",
            locked_defaults={"method": "GET"},
            input_schema=[{"key": "customer_id", "type": "string"}],
        ))
        assert result.error == ""
        assert result.id
        assert result.name == "Get Orders"
        assert result.base_type == "TASK"
        assert result.locked_defaults == {"method": "GET"}

    async def test_create_validation_error_missing_name(self, create_uc):
        result = await create_uc.execute(CreateNodeDefinitionCommand(
            tenant_id="t1", name="", base_worker_id="w1",
        ))
        assert result.error_code == "VALIDATION_ERROR"
        assert any("name" in e for e in result.error_details.get("errors", []))

    async def test_create_validation_error_missing_worker(self, create_uc):
        result = await create_uc.execute(CreateNodeDefinitionCommand(
            tenant_id="t1", name="Test", base_type="TASK",
        ))
        assert result.error_code == "VALIDATION_ERROR"
        assert any("base_worker_id" in e for e in result.error_details.get("errors", []))

    async def test_create_non_task_no_worker_needed(self, create_uc):
        result = await create_uc.execute(CreateNodeDefinitionCommand(
            tenant_id="t1", name="Custom Condition", base_type="CONDITION",
        ))
        assert result.error == ""
        assert result.base_type == "CONDITION"

    async def test_create_with_data_mode(self, create_uc):
        result = await create_uc.execute(CreateNodeDefinitionCommand(
            tenant_id="t1", name="CSV Node", base_worker_id="w1",
            data_mode="csv_single", edit_mode="before_dispatch",
        ))
        assert result.error == ""
        assert result.data_mode == "csv_single"
        assert result.edit_mode == "before_dispatch"

    async def test_create_default_data_mode(self, create_uc):
        result = await create_uc.execute(CreateNodeDefinitionCommand(
            tenant_id="t1", name="Default", base_worker_id="w1",
        ))
        assert result.data_mode == "json"
        assert result.edit_mode == "none"

    async def test_create_invalid_data_mode(self, create_uc):
        result = await create_uc.execute(CreateNodeDefinitionCommand(
            tenant_id="t1", name="Bad", base_worker_id="w1",
            data_mode="invalid",
        ))
        assert result.error_code == "VALIDATION_ERROR"
        assert any("data_mode" in e for e in result.error_details.get("errors", []))


class TestGetNodeDefinition:

    async def test_get_existing(self, create_uc, get_uc):
        created = await create_uc.execute(CreateNodeDefinitionCommand(
            tenant_id="t1", name="ND1", base_worker_id="w1",
        ))
        result = await get_uc.execute(GetNodeDefinitionCommand(node_definition_id=created.id))
        assert result.error == ""
        assert result.name == "ND1"

    async def test_get_not_found(self, get_uc):
        result = await get_uc.execute(GetNodeDefinitionCommand(node_definition_id="nonexistent"))
        assert "not found" in result.error


class TestListNodeDefinitions:

    async def test_list_by_tenant(self, create_uc, list_uc):
        await create_uc.execute(CreateNodeDefinitionCommand(tenant_id="t1", name="A", base_worker_id="w1"))
        await create_uc.execute(CreateNodeDefinitionCommand(tenant_id="t1", name="B", base_worker_id="w2"))
        await create_uc.execute(CreateNodeDefinitionCommand(tenant_id="t2", name="C", base_worker_id="w3"))

        result = await list_uc.execute(ListNodeDefinitionsCommand(tenant_id="t1"))
        assert result.total == 2
        names = {item["name"] for item in result.items}
        assert names == {"A", "B"}

    async def test_list_empty(self, list_uc):
        result = await list_uc.execute(ListNodeDefinitionsCommand(tenant_id="empty"))
        assert result.total == 0
        assert result.items == []


class TestUpdateNodeDefinition:

    async def test_patch_fields(self, create_uc, update_uc, get_uc):
        created = await create_uc.execute(CreateNodeDefinitionCommand(
            tenant_id="t1", name="Original", base_worker_id="w1",
        ))
        result = await update_uc.execute(UpdateNodeDefinitionCommand(
            node_definition_id=created.id,
            name="Updated",
            icon="Star",
            locked_defaults={"timeout": 30},
        ))
        assert result.error == ""
        assert result.name == "Updated"
        assert result.icon == "Star"
        assert result.locked_defaults == {"timeout": 30}

        fetched = await get_uc.execute(GetNodeDefinitionCommand(node_definition_id=created.id))
        assert fetched.name == "Updated"

    async def test_patch_not_found(self, update_uc):
        result = await update_uc.execute(UpdateNodeDefinitionCommand(
            node_definition_id="missing", name="X",
        ))
        assert "not found" in result.error

    async def test_patch_data_mode(self, create_uc, update_uc, get_uc):
        created = await create_uc.execute(CreateNodeDefinitionCommand(
            tenant_id="t1", name="Original", base_worker_id="w1",
        ))
        result = await update_uc.execute(UpdateNodeDefinitionCommand(
            node_definition_id=created.id,
            data_mode="csv_multi",
            edit_mode="after_dispatch",
        ))
        assert result.error == ""
        assert result.data_mode == "csv_multi"
        assert result.edit_mode == "after_dispatch"

        fetched = await get_uc.execute(GetNodeDefinitionCommand(node_definition_id=created.id))
        assert fetched.data_mode == "csv_multi"

    async def test_patch_validation_error(self, create_uc, update_uc):
        created = await create_uc.execute(CreateNodeDefinitionCommand(
            tenant_id="t1", name="OK", base_worker_id="w1",
        ))
        result = await update_uc.execute(UpdateNodeDefinitionCommand(
            node_definition_id=created.id, name="",
        ))
        assert result.error_code == "VALIDATION_ERROR"
        assert any("name" in e for e in result.error_details.get("errors", []))


class TestDeleteNodeDefinition:

    async def test_delete_existing(self, create_uc, delete_uc, get_uc):
        created = await create_uc.execute(CreateNodeDefinitionCommand(
            tenant_id="t1", name="ToDelete", base_worker_id="w1",
        ))
        result = await delete_uc.execute(DeleteNodeDefinitionCommand(node_definition_id=created.id))
        assert result.deleted is True

        fetched = await get_uc.execute(GetNodeDefinitionCommand(node_definition_id=created.id))
        assert "not found" in fetched.error

    async def test_delete_not_found(self, delete_uc):
        result = await delete_uc.execute(DeleteNodeDefinitionCommand(node_definition_id="missing"))
        assert "not found" in result.error
