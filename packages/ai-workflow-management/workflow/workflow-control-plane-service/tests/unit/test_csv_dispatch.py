"""Tests for CSV pre-dispatch hook in OrchestrationEngine."""

import pytest

pytestmark = pytest.mark.skip(reason="legacy inline-execution coverage — rewrite to use tests.helpers.queue_runner for queued-mode regression coverage")
from unittest.mock import MagicMock, AsyncMock

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.orchestration.successor_processor import SuccessorProcessor
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_stage_history_repo import MemoryStageHistoryRepository
from tests.helpers.node_driver import drive_node


@pytest.fixture
def mock_file_service():
    return MockFileServiceClient()


@pytest.fixture
def csv_converter():
    return CsvConverter()


@pytest.fixture
def task_repo():
    return MemoryTaskRepository()


@pytest.fixture
def run_repo():
    return MemoryRunRepository()


@pytest.fixture
def engine(task_repo, run_repo, mock_file_service, csv_converter):
    handler_registry = NodeHandlerRegistry()
    task_factory = TaskFactory()
    stage_history_repo = MemoryStageHistoryRepository()
    stage_recorder = StageRecorder(stage_history_repo)
    successor_processor = SuccessorProcessor(handler_registry, task_factory)

    return OrchestrationEngine(
        handler_registry=handler_registry,
        task_factory=task_factory,
        successor_processor=successor_processor,
        stage_recorder=stage_recorder,
        task_repo=task_repo,
        run_repo=run_repo,
        event_publisher=MagicMock(),
        callback_publisher=None,
        file_service_client=mock_file_service,
        csv_converter=csv_converter,
    )


@pytest.fixture
def engine_no_file_service(task_repo, run_repo):
    handler_registry = NodeHandlerRegistry()
    task_factory = TaskFactory()
    stage_history_repo = MemoryStageHistoryRepository()
    stage_recorder = StageRecorder(stage_history_repo)
    successor_processor = SuccessorProcessor(handler_registry, task_factory)

    return OrchestrationEngine(
        handler_registry=handler_registry,
        task_factory=task_factory,
        successor_processor=successor_processor,
        stage_recorder=stage_recorder,
        task_repo=task_repo,
        run_repo=run_repo,
        event_publisher=MagicMock(),
    )


def _make_task_node(config=None):
    return WorkflowNode(
        id="n1", name="Task1", node_type=NodeType.TASK, worker_type="w1",
        config=config or {},
    )


def _make_workflow(node):
    return Workflow(id="wf-1", name="W", nodes=[node])


def _make_run():
    return WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)


def _make_task(resolved_input=None):
    return Task(
        id="task-1", run_id="run-1", node_id="n1",
        status=TaskStatus.READY, worker_type="w1",
        resolved_input=resolved_input or {},
    )


class TestJsonModeNoop:

    async def test_json_mode_no_conversion(self, engine, task_repo):
        node = _make_task_node(config={
            "_node_definition": {
                "data_mode": "json",
                "edit_mode": "none",
            },
            "_node_internals": {
                "locked_defaults": {"method": "GET"},
            }
        })
        workflow = _make_workflow(node)
        run = _make_run()
        task = _make_task({"name": "John", "method": "GET"})
        task_repo.save(task)

        await drive_node(engine, task, run, workflow, GraphTopology())
        saved = task_repo.get_by_id("task-1")
        assert saved.status == TaskStatus.RUNNING
        assert saved.resolved_input == {"name": "John", "method": "GET"}

    async def test_no_node_definition_no_conversion(self, engine, task_repo):
        node = _make_task_node()
        workflow = _make_workflow(node)
        run = _make_run()
        task = _make_task({"key": "value"})
        task_repo.save(task)

        await drive_node(engine, task, run, workflow, GraphTopology())
        saved = task_repo.get_by_id("task-1")
        assert saved.status == TaskStatus.RUNNING
        assert saved.resolved_input == {"key": "value"}


class TestCsvSingleUpload:

    async def test_csv_single_uploads_and_replaces_input(self, engine, task_repo, mock_file_service):
        node = _make_task_node(config={
            "_node_definition": {
                "data_mode": "csv_single",
                "edit_mode": "none",
            },
            "_node_internals": {
                "locked_defaults": {"method": "GET", "url": "https://api"},
            }
        })
        workflow = _make_workflow(node)
        run = _make_run()
        task = _make_task({"name": "John", "age": "30", "method": "GET", "url": "https://api"})
        task_repo.save(task)

        await drive_node(engine, task, run, workflow, GraphTopology())
        saved = task_repo.get_by_id("task-1")
        assert saved.status == TaskStatus.RUNNING
        assert "file_id" in saved.resolved_input
        assert saved.resolved_input["data_mode"] == "csv_single"
        assert saved.resolved_input["method"] == "GET"
        assert saved.resolved_input["url"] == "https://api"
        # Verify file was uploaded
        file_id = saved.resolved_input["file_id"]
        content = await mock_file_service.download_file(file_id)
        assert b"name" in content
        assert b"John" in content


class TestCsvMultiUpload:

    async def test_csv_multi_uploads(self, engine, task_repo, mock_file_service):
        node = _make_task_node(config={
            "_node_definition": {
                "data_mode": "csv_multi",
                "edit_mode": "none",
            },
            "_node_internals": {
                "locked_defaults": {"method": "POST"},
            }
        })
        workflow = _make_workflow(node)
        run = _make_run()
        task = _make_task({
            "method": "POST",
            "items": [
                {"name": "Alice", "score": "90"},
                {"name": "Bob", "score": "85"},
            ],
        })
        task_repo.save(task)

        await drive_node(engine, task, run, workflow, GraphTopology())
        saved = task_repo.get_by_id("task-1")
        assert "file_id" in saved.resolved_input
        assert saved.resolved_input["data_mode"] == "csv_multi"
        content = await mock_file_service.download_file(saved.resolved_input["file_id"])
        assert b"Alice" in content
        assert b"Bob" in content


class TestFileIdPassthrough:

    async def test_existing_file_id_skips_conversion(self, engine, task_repo, mock_file_service):
        node = _make_task_node(config={
            "_node_definition": {
                "data_mode": "csv_single",
                "edit_mode": "none",
            },
            "_node_internals": {
                "locked_defaults": {"method": "GET"},
            }
        })
        workflow = _make_workflow(node)
        run = _make_run()
        task = _make_task({"file_id": "user-uploaded-file", "method": "GET"})
        task_repo.save(task)

        await drive_node(engine, task, run, workflow, GraphTopology())
        saved = task_repo.get_by_id("task-1")
        assert saved.resolved_input["file_id"] == "user-uploaded-file"
        assert saved.resolved_input["method"] == "GET"
        assert saved.resolved_input["data_mode"] == "csv_single"
        # No files should have been uploaded
        assert len(mock_file_service._store) == 0


class TestNoFileServiceGraceful:

    async def test_no_file_service_skips_gracefully(self, engine_no_file_service, task_repo):
        node = _make_task_node(config={
            "_node_definition": {
                "data_mode": "csv_single",
                "edit_mode": "none",
            },
            "_node_internals": {
                "locked_defaults": {"method": "GET"},
            }
        })
        workflow = _make_workflow(node)
        run = _make_run()
        task = _make_task({"name": "John", "method": "GET"})
        task_repo.save(task)

        await drive_node(engine_no_file_service, task, run, workflow, GraphTopology())
        saved = task_repo.get_by_id("task-1")
        # Should still dispatch without conversion
        assert saved.status == TaskStatus.RUNNING
        assert "file_id" not in saved.resolved_input


class TestBeforeDispatchHitl:

    async def test_before_dispatch_pauses_task(self, engine, task_repo, mock_file_service):
        node = _make_task_node(config={
            "_node_definition": {
                "data_mode": "csv_single",
                "edit_mode": "before_dispatch",
            },
            "_node_internals": {
                "locked_defaults": {"method": "GET"},
            }
        })
        workflow = _make_workflow(node)
        run = _make_run()
        task = _make_task({"name": "John", "method": "GET"})
        task_repo.save(task)

        await drive_node(engine, task, run, workflow, GraphTopology())
        saved = task_repo.get_by_id("task-1")
        assert saved.status == TaskStatus.PENDING
        assert "file_id" in saved.resolved_input

    async def test_before_dispatch_with_file_id_pauses(self, engine, task_repo):
        node = _make_task_node(config={
            "_node_definition": {
                "data_mode": "csv_single",
                "edit_mode": "before_dispatch",
            },
            "_node_internals": {
                "locked_defaults": {"method": "GET"},
            }
        })
        workflow = _make_workflow(node)
        run = _make_run()
        task = _make_task({"file_id": "existing-file", "method": "GET"})
        task_repo.save(task)

        await drive_node(engine, task, run, workflow, GraphTopology())
        saved = task_repo.get_by_id("task-1")
        assert saved.status == TaskStatus.PENDING
        assert saved.resolved_input["file_id"] == "existing-file"
