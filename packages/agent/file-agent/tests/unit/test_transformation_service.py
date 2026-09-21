from app.layer1_domain.entities.transformation import (
    TransformationErrorResult,
    TransformationSuccessResult,
)
from app.layer4_frameworks.impl.transformation_service_impl import (
    DataFactoryService,
)


class RecordingTransformationClient:
    def __init__(self):
        self.calls = []

    def execute_column_payload(self, payload):
        self.calls.append(("column", payload))
        file_id = f"file-{len(self.calls)}"
        return {
            "message": "column-ok",
            "download_url": f"https://example.com/download/{file_id}",
            "file_id": file_id,
        }

    def execute_row_payload(self, payload):
        self.calls.append(("row", payload))
        file_id = f"file-{len(self.calls)}"
        return {
            "message": "row-ok",
            "download_url": f"https://example.com/download/{file_id}",
            "file_id": file_id,
        }


def test_execute_groups_consecutive_steps_and_chains_file_id():
    client = RecordingTransformationClient()
    service = DataFactoryService(client)

    result = service.execute(
        file_id="file-0.csv",
        rules=[
            {
                "scope": "column",
                "rule_name": "rule-1",
                "type": "mapping",
                "params": {},
            },
            {
                "scope": "column",
                "rule_name": "rule-2",
                "type": "mapping",
                "params": {},
            },
            {
                "scope": "row",
                "operation": "delete",
                "row_identifier": {"type": "index", "index": 0},
            },
            {
                "scope": "row",
                "operation": "delete",
                "row_identifier": {"type": "index", "index": 1},
            },
            {
                "scope": "column",
                "rule_name": "rule-3",
                "type": "mapping",
                "params": {},
            },
            {
                "scope": "column",
                "rule_name": "rule-4",
                "type": "mapping",
                "params": {},
            },
        ],
    )

    assert len(client.calls) == 3
    assert client.calls[0][0] == "column"
    assert client.calls[0][1]["file_path"] == "file-0.csv"
    assert client.calls[0][1]["file_format"] == "csv"
    assert len(client.calls[0][1]["rules"]) == 2
    assert client.calls[1][0] == "row"
    assert client.calls[1][1]["file_path"] == "file-1"
    assert len(client.calls[1][1]["operations"]) == 2
    assert client.calls[2][0] == "column"
    assert client.calls[2][1]["file_path"] == "file-2"
    assert len(client.calls[2][1]["rules"]) == 2

    assert isinstance(result, TransformationSuccessResult)
    assert result.file_id == "file-3"
    assert result.batch_count == 3


def test_execute_creates_four_batches_for_mixed_ordered_rules():
    client = RecordingTransformationClient()
    service = DataFactoryService(client)

    result = service.execute(
        file_id="file-0.csv",
        rules=[
            {"scope": "column", "rule_name": "rule-1", "type": "mapping", "params": {}},
            {"scope": "column", "rule_name": "rule-2", "type": "mapping", "params": {}},
            {"scope": "row", "operation": "delete", "row_identifier": {"type": "index", "index": 0}},
            {"scope": "column", "rule_name": "rule-3", "type": "mapping", "params": {}},
            {"scope": "row", "operation": "delete", "row_identifier": {"type": "index", "index": 1}},
            {"scope": "row", "operation": "delete", "row_identifier": {"type": "index", "index": 2}},
        ],
    )

    assert len(client.calls) == 4
    assert client.calls[0][0] == "column"
    assert len(client.calls[0][1]["rules"]) == 2
    assert client.calls[1][0] == "row"
    assert len(client.calls[1][1]["operations"]) == 1
    assert client.calls[2][0] == "column"
    assert len(client.calls[2][1]["rules"]) == 1
    assert client.calls[3][0] == "row"
    assert len(client.calls[3][1]["operations"]) == 2
    assert isinstance(result, TransformationSuccessResult)
    assert result.batch_count == 4
    assert result.file_id == "file-4"


def test_execute_returns_failed_batch_details_when_api_returns_error():
    class FailingTransformationClient(RecordingTransformationClient):
        def execute_row_payload(self, payload):
            self.calls.append(("row", payload))
            return {
                "error": "API error",
                "details": "row failure",
            }

    client = FailingTransformationClient()
    service = DataFactoryService(client)

    result = service.execute(
        file_id="file-0.csv",
        rules=[
            {
                "scope": "column",
                "rule_name": "rule-1",
                "type": "mapping",
                "params": {},
            },
            {
                "scope": "row",
                "operation": "delete",
                "row_identifier": {"type": "index", "index": 0},
            },
        ],
    )

    assert isinstance(result, TransformationErrorResult)
    assert result.error == "API error"
    assert result.failed_batch_index == 2
    assert result.batch_type == "row"
    assert result.last_successful_file_id == "file-1"


def test_execute_uses_extension_from_file_id_as_file_format():
    client = RecordingTransformationClient()
    service = DataFactoryService(client)

    service.execute(
        file_id="file-0.xlsx",
        rules=[
            {
                "scope": "column",
                "rule_name": "rule-1",
                "type": "mapping",
                "params": {},
            }
        ],
    )

    assert client.calls[0][1]["file_format"] == "xlsx"