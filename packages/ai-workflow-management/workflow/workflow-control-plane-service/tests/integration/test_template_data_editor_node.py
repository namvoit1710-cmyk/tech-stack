"""Integration scenario: TEMPLATE_DATA_EDIT node, end-to-end through dispatch.

1. Create a workflow with a single TEMPLATE_DATA_EDIT node whose config carries
   ``template_id`` / ``business_data_model_id`` / ``requester_file_id``.
2. Stub the governance + Integration Hub clients on the registered handler so
   no network is touched; governance returns country/city/street fields.
3. Start a run and drain the queue so the node lands in PENDING.
4. The PENDING task's ``output_metadata.input_schema`` must be the dynamic
   schema: value-help columns are ``SelectTableControl``s bound to the BDM
   search-help endpoint; the table artifact is seeded from the requester file.
5. ``GET /schema`` returns the same rich schema verbatim (StaticSchemaProvider).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.layer1_domain.template_schema import TemplateFieldConfig
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container
from tests.conftest import assert_ok
from tests.helpers.queue_runner import QueueRunner

WORKFLOW_ID = "wf-template-data-edit-1"
NODE_ID = "n_tde"
TEMPLATE_ID = "019ed482-tmpl"
MODEL_ID = "25fa8294-bdm"
HUB_BASE = "https://hub.example.test"

# Headers are the field *aliases* — that's what governance puts in the
# requester file, and what the table columns must bind to.
REQUESTER_CSV = (
    "Country,streetName,cityName\n"
    "GB,PO BOX,BRACKNELL\n"
    "US,Wall St,NEW YORK\n"
)

TEMPLATE_FIELDS = [
    TemplateFieldConfig(
        field="country", alias="Country", cdm_id="country_1",
        value_help=True, binding_kind="DIRECT_LOOKUP", order=4, required=True,
    ),
    TemplateFieldConfig(
        field="streetName", alias="streetName", cdm_id="street_1",
        value_help=False, order=5,
    ),
    TemplateFieldConfig(
        field="cityName", alias="cityName", cdm_id="city_1",
        value_help=True, binding_kind="DEPENDENT_LOOKUP",
        dependency_fields=("country_1",), order=6,
    ),
]


class _StubGovernance:
    async def get_template_fields(self, template_id):
        assert template_id == TEMPLATE_ID
        return list(TEMPLATE_FIELDS)


class _StubHub:
    base_url = HUB_BASE


@pytest.fixture
def container():
    c = build_app_container()
    # Swap the registered handler's external clients for stubs (no network).
    handler = c["get_node_schema"]._handler_registry.get(NodeType.TEMPLATE_DATA_EDIT)
    handler._governance = _StubGovernance()
    handler._hub = _StubHub()
    return c


@pytest.fixture
def client(container):
    return TestClient(create_fastapi_app(container))


async def _drain(container) -> None:
    engine = container["_orchestration_worker"]._engine
    queue = container["_orchestration_worker"]._queue
    await QueueRunner(engine, queue).run_until_idle()


async def _seed_workflow(container) -> str:
    from app.layer1_domain.entities.workflow import Workflow, WorkflowNode

    fs = container["file_service_client"]
    up = await fs.upload_file(REQUESTER_CSV.encode("utf-8"), "requester.csv", "text/csv")
    requester_file_id = up["file_id"]

    wf = Workflow(
        id=WORKFLOW_ID,
        name="Template Data Editor",
        nodes=[
            WorkflowNode(
                id=NODE_ID,
                name="Template Editor",
                node_type=NodeType.TEMPLATE_DATA_EDIT,
                config={
                    "template_id": TEMPLATE_ID,
                    "business_data_model_id": MODEL_ID,
                    "requester_file_id": requester_file_id,
                    "data_mode": "csv_multi",
                },
            ),
        ],
        edges=[],
    )
    container["get_node_schema"].workflow_repo.save(wf)
    return requester_file_id


def _list_tasks(container, run_id):
    return container["edit_node_data"].task_repo.list_by_run(run_id)


def _table_cells(schema: list) -> dict:
    table = schema[0]
    row = table["fields"][0]
    return {c["key"]: c for c in row["fields"]}


@pytest.mark.asyncio
async def test_template_data_edit_dispatch_builds_dynamic_schema(client, container):
    requester_file_id = await _seed_workflow(container)

    r = client.post("/api/v1/runs", json={"workflow_id": WORKFLOW_ID, "input_data": {}})
    run_id = assert_ok(r)["id"]
    await _drain(container)

    # 1. The PENDING task carries the dynamic schema on its output_metadata.
    task = next(t for t in _list_tasks(container, run_id) if t.node_id == NODE_ID)
    meta = task.output_metadata or {}
    assert meta.get("node_type") == "TEMPLATE_DATA_EDIT"
    assert meta.get("template_id") == TEMPLATE_ID

    schema = meta.get("input_schema") or []
    assert schema, "input_schema must be stamped at dispatch"
    cells = _table_cells(schema)
    # Columns are keyed by the alias-based column key (the file's headers).
    assert set(cells) == {"Country", "streetName", "cityName"}

    # value-help columns → SelectTableControl bound to the search-help service
    country = cells["Country"]["fieldConfig"]
    assert country["fieldControl"] == "SelectTableControl"
    country_so = country["controlProps"]["serverOptions"]
    assert country_so["service"] == "integration-hub"
    assert country_so["endpoint"] == (
        f"/business-data-models/{MODEL_ID}/bdm-fields/country_1/search-help/rows"
    )
    assert country_so["responseMapping"] == {"dataPath": "items", "totalPath": "totalCount"}
    # plain column → InputControl
    assert cells["streetName"]["fieldConfig"]["fieldControl"] == "InputControl"
    # dependent lookup → contextValues references the parent row column
    city_so = cells["cityName"]["fieldConfig"]["controlProps"]["serverOptions"]
    assert city_so["contextValues"] == {"country_1": "${rowData.country}"}

    # 2. The table artifact is seeded from the requester file.
    working_ref = task.resolved_input
    assert isinstance(working_ref, dict)
    table_art = next(
        a for a in working_ref.get("artifacts", []) if a.get("name") == "table_data"
    )
    assert table_art["file_id"] == requester_file_id

    # 3. GET /schema serves the same rich schema verbatim.
    rs = client.get(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/schema")
    payload = assert_ok(rs)
    served = payload["schema"]
    served_cells = _table_cells(served)
    assert served_cells["country"]["fieldConfig"]["fieldControl"] == "SelectTableControl"
