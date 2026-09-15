"""Regression — successive PATCHes on the same node chain etags.

Before the fix, every PATCH on the same ``(task, node)`` pair emitted a
``NodeDataEdited`` event with an identical ``deterministic_event_id``
(only ``node_id`` was in the correlation key, not the new revision).
The second and later events were silently dropped by the event log's
event_id dedupe. The projector never folded them, the view store
lagged, and PATCH N+1 saw a 412 even though the caller used the etag
returned by PATCH N.

The fix: include ``new_ref["version"]`` in the correlation key so each
successful PATCH emits a distinct event_id.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.data_reference import build_file_ref
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer2_application.node_handlers.schema_providers import (
    DataEditSchemaProvider,
)
from app.layer1_domain.value_objects.node_input_etag import NodeInputEtag
from app.layer1_domain.value_objects import NodeType, RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
    EditNodeDataCommand,
)
from bootstrap import build_app_container


@pytest.mark.asyncio
async def test_successive_patches_chain_etag():
    c = build_app_container()
    fs = c["file_service_client"]

    # Seed a header-only root CSV (the shape the provisioner would have
    # produced for a schema with {name, input} scalars).
    main_up = await fs.upload_file(
        b"__row_id,name,input\n", "root.csv", "text/csv",
    )
    ref = build_file_ref(
        file_id=main_up["file_id"],
        columns=["__row_id", "name", "input"],
    )
    ref["artifact_type"] = "object_field"
    ref["content_type"] = "text/csv"
    wf = Workflow(
        id="wf-1", name="T",
        nodes=[WorkflowNode(
            id="n1", name="N1", node_type=NodeType.DATA_EDIT,
            config={"_node_definition": {"input_schema": [
                {"key": "name", "outputType": "string", "fieldConfig": {}},
                {"key": "input", "outputType": "string", "fieldConfig": {}},
            ]}},
        )],
        edges=[],
    )
    uc = c["edit_node_data"]
    uc.workflow_repo.save(wf)
    uc.run_repo.save(WorkflowRun(
        id="r-1", workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(variables={"n1": ref}),
    ))
    task = Task(
        id="t-1", run_id="r-1", node_id="n1", worker_type="",
        status=TaskStatus.COMPLETED, created_at="2026-01-01T00:00:00Z",
    )
    task.resolved_input = ref
    task.output_metadata = {}
    uc.task_repo.save(task)

    _, schema_version, _ = await DataEditSchemaProvider().resolve(
        node=wf.nodes[0], task=task, run=None, view=None,
    )
    etag = NodeInputEtag.of(ref, schema_version=schema_version)

    # PATCH #1 — upsert row 1 with "first".
    r1 = await uc.execute(EditNodeDataCommand(
        run_id="r-1", node_id="n1", if_match=etag,
        artifacts=[{
            "artifact_id": "__root__",
            "operations": [{
                "operation": "insert", "row_id": None,
                "data": {"input": "first"},
            }],
        }],
    ))
    assert r1.error is None, f"PATCH #1 failed: {r1.error}"
    assert r1.version == 1

    # PATCH #2 with #1's returned etag — upsert replaces same row.
    r2 = await uc.execute(EditNodeDataCommand(
        run_id="r-1", node_id="n1", if_match=r1.etag,
        artifacts=[{
            "artifact_id": "__root__",
            "operations": [{
                "operation": "insert", "row_id": None,
                "data": {"input": "second"},
            }],
        }],
    ))
    assert r2.error is None, f"PATCH #2 failed: {r2.error}"
    assert r2.version == 2

    # PATCH #3 with #2's returned etag — this was the regression point.
    r3 = await uc.execute(EditNodeDataCommand(
        run_id="r-1", node_id="n1", if_match=r2.etag,
        artifacts=[{
            "artifact_id": "__root__",
            "operations": [{
                "operation": "insert", "row_id": None,
                "data": {"input": "third"},
            }],
        }],
    ))
    assert r3.error is None, f"PATCH #3 failed: {r3.error}"
    assert r3.version == 3

    # The view store must reflect the final PATCH's state.
    view = c["run_context_view_store"].get("r-1")
    stored = view.variables.get("n1")
    assert isinstance(stored, dict)
    assert stored["version"] == 3
