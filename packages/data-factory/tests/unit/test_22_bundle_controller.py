"""HTTP surface of the bundle endpoint: request parsing, status codes, response shape.

The use case is a double here; what is under test is the controller — that the
DTO accepts the documented body, that the command it builds carries every field
through, and that a bad request is a 400 rather than a 200 with an empty result.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.layer3_adapters.controllers.restful.v1 import bundle_controller
from app.layer2_application.features.bundle.use_cases.transform_validate_bundle_usecase import (
    BundleTableResult,
    TransformValidateBundleResult,
)


class _RecordingUseCase:
    def __init__(self, result=None):
        self.commands = []
        self.result = result

    async def execute(self, command):
        self.commands.append(command)
        if self.result is not None:
            return self.result
        return TransformValidateBundleResult(
            success=True,
            source_file_id=command.source_file_id,
            tables=[
                BundleTableResult(
                    name=table.name,
                    success=True,
                    output_file_id=f"file-{table.name}",
                    output_version_id="v1",
                    total_rows=3,
                    total_columns=4,
                    validation={"total_rows": 3, "valid_rows": 2, "invalid_rows": 1,
                                "result_file_id": "res-1", "result_file_url": "",
                                "message": "ok"},
                )
                for table in command.tables
            ],
            table_file_ids={t.name: f"file-{t.name}" for t in command.tables},
            total_rows=3 * len(command.tables),
            invalid_rows=len(command.tables),
            message=f"{len(command.tables)}/{len(command.tables)} tables completed.",
        )


def _client(usecase=None, wired=True):
    app = FastAPI()
    app.include_router(bundle_controller.router, prefix="/api/v1/bundle")
    usecase = usecase or _RecordingUseCase()
    app.state.container = {"transform_validate_bundle_usecase": usecase} if wired else {}
    return TestClient(app), usecase


BODY = {
    "source_file_id": "wb-1",
    "file_format": "xlsx",
    "output_format": "csv",
    "default_header_row": 2,
    "tables": [
        {
            "name": "ArticleMaster",
            "sheet_names": ["ArticleMaster"],
            "header_row": 2,
            "rules": [{"type": "rename_columns", "params": {"mapping": {"a": "b"}}}],
            "validation_rules": [{
                "rule_name": "LENGTH_40", "type": "expression",
                "params": {"columns": ["baseUnit"],
                           "expression": "pl.col('baseUnit').str.len_chars() <= 40"}}],
        },
        {
            "name": "ARTMasterPurchasing",
            "sheet_names": ["ARTMasterPurchasing"],
            "depends_on": ["ArticleMaster"],
            "rules": [{"type": "join_reference",
                       "params": {"table": "ArticleMaster", "on": ["itemID"]}}],
            "validation_rule_set_id": "ART41.00-Purchasing",
        },
    ],
}


def test_a_documented_bundle_body_is_accepted_and_answered():
    client, _ = _client()

    response = client.post("/api/v1/bundle/transform-validate", json=BODY)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["source_file_id"] == "wb-1"
    assert [t["name"] for t in body["tables"]] == ["ArticleMaster", "ARTMasterPurchasing"]
    assert body["table_file_ids"]["ArticleMaster"] == "file-ArticleMaster"
    assert body["tables"][0]["validation"]["invalid_rows"] == 1


def test_every_field_reaches_the_command():
    client, usecase = _client()

    client.post("/api/v1/bundle/transform-validate", json=BODY)

    command = usecase.commands[0]
    assert command.file_format == "xlsx"
    assert command.output_format == "csv"
    assert command.default_header_row == 2
    first, second = command.tables
    assert first.header_row == 2
    assert first.sheet_names == ["ArticleMaster"]
    assert first.validation_rules[0]["rule_name"] == "LENGTH_40"
    assert second.depends_on == ["ArticleMaster"]
    assert second.validation_rule_set_id == "ART41.00-Purchasing"
    assert second.rules[0]["params"]["table"] == "ArticleMaster"


def test_file_path_is_accepted_as_an_alias_for_source_file_id():
    client, usecase = _client()
    body = {**BODY}
    body.pop("source_file_id")
    body["file_path"] = "wb-alias"

    response = client.post("/api/v1/bundle/transform-validate", json=body)

    assert response.status_code == 200
    assert usecase.commands[0].source_file_id == "wb-alias"


def test_a_body_with_no_source_file_is_a_400():
    client, _ = _client()
    body = {**BODY}
    body.pop("source_file_id")

    response = client.post("/api/v1/bundle/transform-validate", json=body)

    assert response.status_code == 400
    assert "source_file_id" in response.json()["detail"]


def test_a_body_with_no_tables_is_a_400():
    client, _ = _client()

    response = client.post("/api/v1/bundle/transform-validate", json={**BODY, "tables": []})

    assert response.status_code == 400
    assert "table" in response.json()["detail"].lower()


def test_a_table_without_a_name_is_a_422():
    """Pydantic owns shape validation; the controller should not have to."""
    client, _ = _client()
    body = {**BODY, "tables": [{"sheet_names": ["X"]}]}

    response = client.post("/api/v1/bundle/transform-validate", json=body)

    assert response.status_code == 422


def test_a_partial_run_is_reported_not_hidden():
    """Some tables failing is a 200 carrying the detail, not an opaque error."""
    partial = TransformValidateBundleResult(
        success=False,
        source_file_id="wb-1",
        tables=[
            BundleTableResult(name="A", success=True, output_file_id="file-A"),
            BundleTableResult(name="B", success=False, message="Transform failed: boom"),
        ],
        table_file_ids={"A": "file-A"},
        message="1/2 tables completed. Failed: ['B'].",
    )
    client, _ = _client(_RecordingUseCase(result=partial))

    response = client.post("/api/v1/bundle/transform-validate", json=BODY)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["tables"][1]["message"] == "Transform failed: boom"
    assert "Failed: ['B']" in body["message"]


def test_a_run_that_produced_nothing_is_a_400():
    nothing = TransformValidateBundleResult(
        success=False, source_file_id="wb-1", tables=[],
        message="Table names must be unique within a bundle; repeated: ['A'].")
    client, _ = _client(_RecordingUseCase(result=nothing))

    response = client.post("/api/v1/bundle/transform-validate", json=BODY)

    assert response.status_code == 400
    assert "unique" in response.json()["detail"]


def test_an_unwired_container_is_a_500_not_a_crash():
    client, _ = _client(wired=False)

    response = client.post("/api/v1/bundle/transform-validate", json=BODY)

    assert response.status_code == 500
    assert "not wired" in response.json()["detail"]


def test_optional_table_fields_default_sensibly():
    client, usecase = _client()
    body = {"source_file_id": "wb-1", "tables": [{"name": "OnlyName"}]}

    response = client.post("/api/v1/bundle/transform-validate", json=body)

    assert response.status_code == 200
    table = usecase.commands[0].tables[0]
    assert table.sheet_names is None
    assert table.header_row is None
    assert table.rules == []
    assert table.validation_rules is None
    assert table.result_mode == "errors_only"
    assert table.depends_on == []
