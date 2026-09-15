"""Unit: PATCH /data on a node's ``__root__`` object artifact upserts locally.

Regression for the production "file_id is required" 400 on INPUT nodes whose
schema is a flat scalar form (the common case). The FE PATCHes the node's
``__root__`` artifact with a single ``insert`` carrying the form values. Such
an object artifact must upsert its single row **locally** (csv_ops) and must
NOT be forwarded to the data-factory row-transform service — the factory
rejects the (initially empty) ``file_id`` with ``INVALID_INPUT``.

``EditNodeDataUseCase.execute`` routes ``__root__`` to the local object path
when ``artifact_type == "object_field"`` (what the provisioner stamps) and to
the data-factory otherwise. The bug: when the node's working-ref is the
``build_file_ref(file_id="")`` fallback placeholder — which carries **no**
``artifact_type`` — ``"" != "object_field"`` fell through to the factory, and
the factory rejects the empty ``file_id`` with ``INVALID_INPUT`` → 400. The fix
treats a root artifact as an object unless it is an explicit array type, so the
placeholder upserts locally. These tests pin both the explicit ``object_field``
root and the bare placeholder to the local path.
"""

from __future__ import annotations

import pytest

from app.layer1_domain.entities.data_reference import build_file_ref
from app.layer1_domain.value_objects.node_input_etag import (
    NodeInputEtag,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
    EditNodeDataCommand,
    EditNodeDataUseCase,
)


pytestmark = pytest.mark.asyncio

RUN_ID = "run-1"
NODE_ID = "input-node"
TASK_ID = "run-1-input-node"


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _Run:
    id = RUN_ID
    workflow_id = "wf-1"


class _Node:
    id = NODE_ID
    node_type = NodeType.INPUT


class _Workflow:
    def get_node(self, node_id):
        return _Node() if node_id == NODE_ID else None


class _Task:
    def __init__(self):
        self.id = TASK_ID
        self.node_id = NODE_ID
        self.status = TaskStatus.PENDING
        self.run_generation = 0
        self.resolved_input = build_file_ref(file_id="")
        self.output_metadata = {}


class _RunRepo:
    async def aget_by_id(self, run_id):
        return _Run()


class _TaskRepo:
    def __init__(self, task):
        self._task = task

    async def alist_by_run(self, run_id):
        return [self._task]


class _View:
    def __init__(self, variables):
        self.variables = variables


class _ViewStore:
    def __init__(self, variables):
        self._view = _View(variables)

    def get(self, run_id):
        return self._view


class _Engine:
    def __init__(self):
        self.edited = []

    def load_workflow_for_run(self, run):
        return _Workflow()

    def emit_node_data_edited(self, *a, **k):
        self.edited.append(k)


class _FileService:
    """Mints a fresh file_id per upload; records what it stored."""

    def __init__(self):
        self.uploads = []
        self._n = 0

    async def download_file(self, file_id):
        return b""

    async def upload_file(self, content, filename, content_type):
        self._n += 1
        self.uploads.append(content)
        return {"file_id": f"fid-{self._n}"}

    async def delete_file(self, file_id):
        pass


class _FactorySpy:
    """Fails loudly if the object path ever forwards to the factory."""

    def __init__(self):
        self.calls = 0

    async def apply_row_operations(self, *a, **k):
        self.calls += 1
        raise AssertionError(
            "object __root__ artifact must not be forwarded to the data-factory"
        )


def _build_use_case(variables):
    factory = _FactorySpy()
    use_case = EditNodeDataUseCase(
        logger=None,
        run_repo=_RunRepo(),
        run_context_view_store=_ViewStore(variables),
        task_repo=_TaskRepo(_Task()),
        workflow_repo=None,
        file_service_client=_FileService(),
        node_input_etag_calculator=NodeInputEtag(),
        node_data_factory_service=factory,
        orchestration_engine=_Engine(),
    )
    return use_case, factory


async def _run_insert(working_ref):
    use_case, factory = _build_use_case({NODE_ID: working_ref})
    etag = NodeInputEtag().of(working_ref, schema_version="v0")
    cmd = EditNodeDataCommand(
        run_id=RUN_ID,
        node_id=NODE_ID,
        task_id=TASK_ID,
        if_match=etag,
        artifacts=[{
            "artifact_id": "__root__",
            "operations": [{
                "operation": "insert",
                "row_id": None,
                "data": {"triggerId": "ds", "description": "dsd", "objectType": "1"},
            }],
        }],
    )
    result = await use_case.execute(cmd)
    return result, factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_object_field_root_insert_upserts_locally_not_via_factory():
    """A provisioned ``object_field`` root with an empty file_id upserts via the
    local path — never the data-factory."""
    root = build_file_ref(file_id="")
    root["artifact_type"] = "object_field"

    result, factory = await _run_insert(root)

    assert result.error is None, result.error
    assert factory.calls == 0
    assert result.version == 1
    assert len(result.results) == 1
    assert result.results[0].status == "ok"


async def test_bare_root_placeholder_routes_to_object_path():
    """A working-ref fallback placeholder (build_file_ref → no
    ``artifact_type``) is treated as an object root, not an array."""
    root = build_file_ref(file_id="")
    assert "artifact_type" not in root

    result, factory = await _run_insert(root)

    assert result.error is None, result.error
    assert factory.calls == 0
    assert result.version == 1
