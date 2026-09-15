"""Tests for TestNodeDefinitionUseCase."""

import pytest

from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.resolve.variable_resolver import VariableResolver, TraversalResolverStrategy
from app.layer2_application.features.test_node_definition.use_cases.test_node_definition_use_case import (
    TestNodeDefinitionUseCase,
    TestNodeDefinitionCommand,
)
from app.layer4_frameworks.persistence.memory.memory_node_definition_repo import (
    MemoryNodeDefinitionRepository,
)
from tests.conftest import DummyLogger


class MockWorkerClient:
    """Fake worker registry client that records calls and returns a canned result."""

    def __init__(self, result=None):
        self.calls = []
        self._result = result or {
            "status": "COMPLETED",
            "outputs": {"result": "hello"},
            "output_reference": "f-out-1",
            "execution_time_ms": 42,
        }

    def execute_task(self, worker_type, input_data, node_config=None):
        self.calls.append({
            "worker_type": worker_type,
            "input_data": input_data,
            "node_config": node_config,
        })
        return self._result


class FailingWorkerClient:
    def execute_task(self, worker_type, input_data, node_config=None):
        raise ConnectionError("Worker unreachable")


def _engine_registry(resolver):
    """A NodeHandlerRegistry with the inline-testable ENGINE handlers."""
    from app.layer2_application.node_handlers.base import NodeHandlerRegistry
    from app.layer2_application.node_handlers.condition_handler import ConditionNodeHandler
    from app.layer2_application.node_handlers.switch_handler import SwitchNodeHandler
    from app.layer2_application.node_handlers.compute_handler import ComputeNodeHandler
    from app.layer2_application.node_handlers.set_variable_handler import SetVariableHandler
    from app.layer2_application.node_handlers.merge_handler import MergeNodeHandler
    from app.layer2_application.resolve.variable_resolver import ConditionEvaluator

    reg = NodeHandlerRegistry()
    reg.register(NodeType.CONDITION, ConditionNodeHandler(ConditionEvaluator(resolver)))
    reg.register(NodeType.SWITCH, SwitchNodeHandler(resolver))
    reg.register(NodeType.COMPUTE, ComputeNodeHandler(resolver))
    reg.register(NodeType.SET_VARIABLE, SetVariableHandler(resolver))
    reg.register(NodeType.MERGE, MergeNodeHandler())
    return reg


def _build(worker_client=None, resolver=None, handler_registry=None):
    logger = DummyLogger()
    repo = MemoryNodeDefinitionRepository()
    uc = TestNodeDefinitionUseCase(
        logger=logger,
        node_definition_repo=repo,
        worker_registry_client=worker_client,
        variable_resolver=resolver,
        handler_registry=handler_registry,
    )
    return uc, repo


def _make_node_def(**overrides):
    defaults = dict(
        id="nd-1",
        tenant_id="t1",
        name="Test Node",
        base_type=NodeType.TASK,
        base_worker_id="http_request",
    )
    defaults.update(overrides)
    return NodeDefinition(**defaults)


# ── Happy path ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_successful_execution():
    client = MockWorkerClient()
    uc, repo = _build(worker_client=client)
    repo.save(_make_node_def())

    result = await uc.execute(TestNodeDefinitionCommand(
        node_definition_id="nd-1",
        input_data={"url": "https://example.com"},
    ))

    assert result.error == ""
    assert result.status == "COMPLETED"
    assert result.worker_type == "http_request"
    assert result.outputs == {"result": "hello"}
    assert result.output_reference == "f-out-1"
    assert result.execution_time_ms == 42
    assert result.resolved_input == {"url": "https://example.com"}
    assert len(client.calls) == 1
    assert client.calls[0]["worker_type"] == "http_request"


# ── Error cases ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_node_definition_not_found():
    client = MockWorkerClient()
    uc, repo = _build(worker_client=client)

    result = await uc.execute(TestNodeDefinitionCommand(node_definition_id="missing"))

    assert "not found" in result.error
    assert len(client.calls) == 0


@pytest.mark.asyncio
async def test_pending_node_definition_not_testable_standalone():
    # PENDING types (INPUT/HUMAN_ACTION/…) cannot be tested in isolation.
    resolver = VariableResolver(strategies=[TraversalResolverStrategy()])
    uc, repo = _build(handler_registry=_engine_registry(resolver), resolver=resolver)
    repo.save(_make_node_def(base_type=NodeType.INPUT, base_worker_id=None))

    result = await uc.execute(TestNodeDefinitionCommand(node_definition_id="nd-1"))

    assert result.error_code == "NODE_NOT_TESTABLE_STANDALONE"
    assert result.error_details.get("base_type") == "INPUT"
    assert result.error_details.get("reason") == "pending"


@pytest.mark.asyncio
async def test_deferring_engine_node_definition_not_testable_standalone():
    # LOOP is ENGINE runtime but defers real work — not testable standalone.
    resolver = VariableResolver(strategies=[TraversalResolverStrategy()])
    uc, repo = _build(handler_registry=_engine_registry(resolver), resolver=resolver)
    repo.save(_make_node_def(base_type=NodeType.LOOP, base_worker_id=None))

    result = await uc.execute(TestNodeDefinitionCommand(node_definition_id="nd-1"))

    assert result.error_code == "NODE_NOT_TESTABLE_STANDALONE"
    assert result.error_details.get("base_type") == "LOOP"


@pytest.mark.asyncio
async def test_condition_node_definition_executes_inline():
    resolver = VariableResolver(strategies=[TraversalResolverStrategy()])
    uc, repo = _build(handler_registry=_engine_registry(resolver), resolver=resolver)
    repo.save(_make_node_def(
        base_type=NodeType.CONDITION,
        base_worker_id=None,
        locked_defaults={"expression": "{{$input.x}} > 5"},
    ))

    result = await uc.execute(TestNodeDefinitionCommand(
        node_definition_id="nd-1", input_data={"x": 10},
    ))

    assert result.error == ""
    assert result.status == "COMPLETED"
    assert result.node_type == "CONDITION"
    assert result.branch == "then"
    assert result.outputs["result"] is True


@pytest.mark.asyncio
async def test_worker_id_prefix_normalized_reaches_worker():
    # base_worker_id stored as the catalog id is normalized to the bare type
    # by the entity, so the worker call uses "http_request".
    client = MockWorkerClient()
    uc, repo = _build(worker_client=client)
    repo.save(_make_node_def(base_worker_id="worker:http_request"))

    result = await uc.execute(TestNodeDefinitionCommand(node_definition_id="nd-1"))

    assert result.status == "COMPLETED"
    assert result.worker_type == "http_request"
    assert client.calls[0]["worker_type"] == "http_request"


@pytest.mark.asyncio
async def test_missing_base_worker_id():
    client = MockWorkerClient()
    uc, repo = _build(worker_client=client)
    repo.save(_make_node_def(base_worker_id=None, parent_definition_id="parent"))

    result = await uc.execute(TestNodeDefinitionCommand(node_definition_id="nd-1"))

    assert result.error_code == "VALIDATION_ERROR"
    assert result.error_details.get("node_definition_id") == "nd-1"


@pytest.mark.asyncio
async def test_missing_worker_registry_client():
    uc, repo = _build(worker_client=None)
    repo.save(_make_node_def())

    result = await uc.execute(TestNodeDefinitionCommand(node_definition_id="nd-1"))

    assert "not configured" in result.error


# ── Input merging ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_locked_defaults_merged_with_input():
    client = MockWorkerClient()
    uc, repo = _build(worker_client=client)
    repo.save(_make_node_def(locked_defaults={"method": "GET", "timeout": 30}))

    result = await uc.execute(TestNodeDefinitionCommand(
        node_definition_id="nd-1",
        input_data={"url": "https://example.com"},
    ))

    assert result.resolved_input == {
        "method": "GET",
        "timeout": 30,
        "url": "https://example.com",
    }


@pytest.mark.asyncio
async def test_code_worker_user_input_fields_reach_worker():
    """Code-worker scenario: the worker's own settings (language/code) are locked
    defaults; the user-defined Input Fields (arr1/arr2) flow through verbatim, so
    the worker receives both the script config and the user data to process."""
    client = MockWorkerClient(result={
        "status": "COMPLETED",
        "outputs": {"result": [1, 2, 3, 4]},
        "execution_time_ms": 5,
    })
    uc, repo = _build(worker_client=client)
    repo.save(_make_node_def(
        base_worker_id="code",
        locked_defaults={
            "language": "javascript",
            "code": "return input.arr1.concat(input.arr2);",
        },
    ))

    result = await uc.execute(TestNodeDefinitionCommand(
        node_definition_id="nd-1",
        input_data={"arr1": [1, 2], "arr2": [3, 4]},
    ))

    assert result.error == ""
    assert result.worker_type == "code"
    # The worker receives the user Input Fields alongside the locked script.
    sent = client.calls[0]["input_data"]
    assert sent["arr1"] == [1, 2]
    assert sent["arr2"] == [3, 4]
    assert sent["language"] == "javascript"
    assert sent["code"] == "return input.arr1.concat(input.arr2);"
    assert result.outputs == {"result": [1, 2, 3, 4]}


@pytest.mark.asyncio
async def test_locked_defaults_override_user_input():
    """locked_defaults always win — they are 'locked' and cannot be overridden by user input."""
    client = MockWorkerClient()
    uc, repo = _build(worker_client=client)
    repo.save(_make_node_def(locked_defaults={"method": "GET", "url": "https://locked.com"}))

    result = await uc.execute(TestNodeDefinitionCommand(
        node_definition_id="nd-1",
        input_data={"url": "https://user-attempt.com", "extra": "kept"},
    ))

    assert result.resolved_input["url"] == "https://locked.com"
    assert result.resolved_input["method"] == "GET"
    assert result.resolved_input["extra"] == "kept"


# ── Worker failure ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_worker_returns_failed_status():
    client = MockWorkerClient(result={
        "status": "FAILED",
        "error": "Connection refused",
        "outputs": {},
    })
    uc, repo = _build(worker_client=client)
    repo.save(_make_node_def())

    result = await uc.execute(TestNodeDefinitionCommand(
        node_definition_id="nd-1",
        input_data={"url": "https://bad.com"},
    ))

    assert result.status == "FAILED"
    assert "Connection refused" in result.error


@pytest.mark.asyncio
async def test_worker_returns_error_status_surfaces_message():
    """A worker that runs but fails the task reports status "error" (worker_sdk
    TaskStatus), not "FAILED". Its message must still reach the result — the old
    `== "FAILED"` gate dropped it, leaving a blank error (e.g. a code node with
    no script: "'code' is required.")."""
    client = MockWorkerClient(result={
        "status": "error",
        "error": "'code' is required.",
        "outputs": {"error_type": "ValueError"},
    })
    uc, repo = _build(worker_client=client)
    repo.save(_make_node_def(base_worker_id="code"))

    result = await uc.execute(TestNodeDefinitionCommand(
        node_definition_id="nd-1",
        input_data={"array_1": [1, 2]},
    ))

    assert result.status == "error"
    assert "'code' is required." in result.error


@pytest.mark.asyncio
async def test_worker_execution_exception():
    uc, repo = _build(worker_client=FailingWorkerClient())
    repo.save(_make_node_def())

    result = await uc.execute(TestNodeDefinitionCommand(
        node_definition_id="nd-1",
        input_data={"url": "https://example.com"},
    ))

    assert result.status == "FAILED"
    assert "Worker unreachable" in result.error


@pytest.mark.asyncio
async def test_worker_not_found_returns_failed():
    """When execute_task returns a FAILED status (e.g. no worker registered)."""
    client = MockWorkerClient(result={
        "status": "FAILED",
        "error": "No worker found for type 'unknown_worker'",
    })
    uc, repo = _build(worker_client=client)
    repo.save(_make_node_def(base_worker_id="unknown_worker"))

    result = await uc.execute(TestNodeDefinitionCommand(
        node_definition_id="nd-1",
        input_data={"url": "https://example.com"},
    ))

    assert result.status == "FAILED"
    assert "No worker found" in result.error


# ── input_mapping ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_input_mapping_transforms_fields():
    """input_mapping transforms user form fields into worker fields."""
    resolver = VariableResolver(strategies=[TraversalResolverStrategy()])
    client = MockWorkerClient()
    uc, repo = _build(worker_client=client, resolver=resolver)
    repo.save(_make_node_def(
        input_mapping={
            "query_params": {"id": "{{$custom.customer_id}}"},
        },
    ))

    result = await uc.execute(TestNodeDefinitionCommand(
        node_definition_id="nd-1",
        input_data={"customer_id": "123"},
    ))

    assert result.error == ""
    # Worker should receive the mapped field
    sent = client.calls[0]["input_data"]
    assert sent["query_params"] == {"id": "123"}
    # Original user field is also present
    assert sent["customer_id"] == "123"


@pytest.mark.asyncio
async def test_input_mapping_with_locked_defaults():
    """locked_defaults override both user input and mapped values."""
    resolver = VariableResolver(strategies=[TraversalResolverStrategy()])
    client = MockWorkerClient()
    uc, repo = _build(worker_client=client, resolver=resolver)
    repo.save(_make_node_def(
        locked_defaults={"method": "GET", "url": "https://api.internal/orders"},
        input_mapping={
            "query_params": {"id": "{{$custom.customer_id}}"},
        },
    ))

    result = await uc.execute(TestNodeDefinitionCommand(
        node_definition_id="nd-1",
        input_data={"customer_id": "456", "url": "https://should-be-overridden.com"},
    ))

    assert result.error == ""
    sent = client.calls[0]["input_data"]
    # locked_defaults always win
    assert sent["method"] == "GET"
    assert sent["url"] == "https://api.internal/orders"
    # mapped value present
    assert sent["query_params"] == {"id": "456"}


@pytest.mark.asyncio
async def test_input_mapping_without_resolver_falls_back():
    """When no resolver is available, input_mapping is skipped gracefully."""
    client = MockWorkerClient()
    uc, repo = _build(worker_client=client, resolver=None)
    repo.save(_make_node_def(
        locked_defaults={"method": "POST"},
        input_mapping={"mapped_field": "{{$custom.x}}"},
    ))

    result = await uc.execute(TestNodeDefinitionCommand(
        node_definition_id="nd-1",
        input_data={"x": "val"},
    ))

    assert result.error == ""
    sent = client.calls[0]["input_data"]
    # Mapping skipped, only locked_defaults + raw input
    assert sent == {"x": "val", "method": "POST"}
    assert "mapped_field" not in sent


# ── output_mapping ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_output_mapping_returns_only_defined_keys():
    """output_mapping filters the raw worker output down to the defined keys
    (mirrors production complete_task), not the full worker response."""
    resolver = VariableResolver(strategies=[TraversalResolverStrategy()])
    client = MockWorkerClient(result={
        "status": "COMPLETED",
        "outputs": {"response_body": {"id": 7}, "status_code": 200, "headers": {"x": "y"}},
        "execution_time_ms": 10,
    })
    uc, repo = _build(worker_client=client, resolver=resolver)
    repo.save(_make_node_def(output_mapping={"data": "{{$_output.response_body}}"}))

    result = await uc.execute(TestNodeDefinitionCommand(node_definition_id="nd-1"))

    assert result.status == "COMPLETED"
    # Only the mapped key survives — raw worker keys are gone.
    assert result.outputs == {"data": {"id": 7}}


@pytest.mark.asyncio
async def test_no_output_mapping_passes_raw_output_through():
    """Without an output_mapping the raw worker output passes through unchanged."""
    resolver = VariableResolver(strategies=[TraversalResolverStrategy()])
    client = MockWorkerClient(result={
        "status": "COMPLETED",
        "outputs": {"response_body": {"id": 7}, "status_code": 200},
        "execution_time_ms": 10,
    })
    uc, repo = _build(worker_client=client, resolver=resolver)
    repo.save(_make_node_def())

    result = await uc.execute(TestNodeDefinitionCommand(node_definition_id="nd-1"))

    assert result.outputs == {"response_body": {"id": 7}, "status_code": 200}


@pytest.mark.asyncio
async def test_output_mapping_skipped_without_resolver():
    """No resolver → output_mapping can't be applied, raw output is returned."""
    client = MockWorkerClient(result={
        "status": "COMPLETED",
        "outputs": {"response_body": {"id": 7}},
        "execution_time_ms": 10,
    })
    uc, repo = _build(worker_client=client, resolver=None)
    repo.save(_make_node_def(output_mapping={"data": "{{$_output.response_body}}"}))

    result = await uc.execute(TestNodeDefinitionCommand(node_definition_id="nd-1"))

    assert result.outputs == {"response_body": {"id": 7}}


@pytest.mark.asyncio
async def test_output_mapping_not_applied_on_failed_status():
    """A FAILED worker run keeps its (empty) raw output; mapping is not applied."""
    resolver = VariableResolver(strategies=[TraversalResolverStrategy()])
    client = MockWorkerClient(result={
        "status": "FAILED",
        "error": "boom",
        "outputs": {},
    })
    uc, repo = _build(worker_client=client, resolver=resolver)
    repo.save(_make_node_def(output_mapping={"data": "{{$_output.response_body}}"}))

    result = await uc.execute(TestNodeDefinitionCommand(node_definition_id="nd-1"))

    assert result.status == "FAILED"
    assert result.outputs == {}
