from __future__ import annotations

import pytest

from app.layer1_domain.entities.execution_plan import ExecutionPlan
from app.layer1_domain.exceptions import InvalidInputError
from app.layer2_application.features.query_file_data import (
    normalize_odata_query,
    query_file_data_impl,
)
from app.layer4_frameworks.tools.convert_csv_to_json import (
    make_convert_csv_to_json_tool,
)
from app.layer4_frameworks.tools.convert_json_to_csv import (
    make_convert_json_to_csv_tool,
)
from app.layer4_frameworks.tools.create_file import make_create_file_tool
from app.layer4_frameworks.tools.get_first_n_rows import make_get_first_n_rows_tool
from app.layer4_frameworks.tools.import_file_to_node import (
    make_import_file_to_node_tool,
)
from app.layer4_frameworks.tools.query_file_data import make_query_file_data_tool
from app.layer4_frameworks.tools.revert_file import make_revert_file_tool
from app.layer4_frameworks.tools.transform_column import make_transform_column_tool
from app.layer4_frameworks.tools.transform_row import make_transform_row_tool


class _PreviewGateway:
    def __init__(self):
        self.calls = []

    def get_odata_preview(self, file_id: str, num_rows: int):
        return {"file_id": file_id, "data": []}

    def query_odata_data(self, file_id: str, odata_query: str = ""):
        self.calls.append((file_id, odata_query))
        return {"data": [{"id": 1}]}

    def download_file_content(self, file_id: str) -> bytes:
        return b"id\n1\n"


class _CsvJsonGateway:
    def fetch_csv_data(self, file_id: str):
        return [{"id": 1}]

    def upload_json_file(self, file_name: str, content: bytes) -> str:
        return "json_123"


class _FileUploadGateway:
    def upload_file(self, file_path: str):
        return {"file_id": "file_123"}


class _SettingsProvider:
    def get_file_download_url(self, file_id: str) -> str:
        return f"https://files.local/{file_id}"


class _Ref:
    def require(self):
        return object()


def _description(tool_obj) -> str:
    return str(getattr(tool_obj, "description", "") or getattr(tool_obj, "__doc__", ""))


def test_odata_rejects_disallowed_filter_control_tokens():
    with pytest.raises(InvalidInputError):
        normalize_odata_query("$filter=name eq 'x'; drop table users")


def test_odata_validates_orderby_shape():
    with pytest.raises(InvalidInputError):
        normalize_odata_query("$orderby=name desc;delete")


def test_query_file_data_uses_injected_gateway():
    gateway = _PreviewGateway()
    result = query_file_data_impl(
        "file_123",
        "$select=id&$filter=id eq 1",
        file_preview_gateway=gateway,
    )
    assert result["read_only"] is True
    assert gateway.calls and gateway.calls[0][0] == "file_123"


def test_public_tool_descriptions_keep_argument_and_behavior_guidance():
    preview = _PreviewGateway()
    settings = _SettingsProvider()
    tools = [
        make_convert_csv_to_json_tool(
            csv_json_gateway=_CsvJsonGateway(),
            settings_provider=settings,
        ),
        make_convert_json_to_csv_tool(
            file_upload_gateway=_FileUploadGateway(),
            settings_provider=settings,
        ),
        make_get_first_n_rows_tool(file_preview_gateway=preview),
        make_query_file_data_tool(file_preview_gateway=preview),
        make_import_file_to_node_tool(workflow_node_gateway=object()),
        make_create_file_tool(
            llm_service_ref=_Ref(), file_upload_gateway=_FileUploadGateway()
        ),
        make_revert_file_tool(llm_service_ref=_Ref(), settings_provider=settings),
        make_transform_column_tool(
            data_factory_gateway_ref=_Ref(),
            structured_llm_gateway_ref=_Ref(),
            file_preview_gateway=preview,
            settings_provider=settings,
        ),
        make_transform_row_tool(
            data_factory_gateway_ref=_Ref(),
            structured_llm_gateway_ref=_Ref(),
            file_preview_gateway=preview,
            settings_provider=settings,
        ),
    ]
    descriptions = {
        str(getattr(tool, "name", "")): _description(tool) for tool in tools
    }
    assert "Args:" in descriptions["get_first_n_rows"]
    assert "READ-ONLY" in descriptions["query_file_data"]
    assert "Always produces a NEW file" in descriptions["transform_column"]
    assert "Always creates a NEW file" in descriptions["transform_row"]
    assert "Use this tool only when" in descriptions["import_file_to_node"]
    assert "description" in descriptions["create_file"]
    assert "conversation_history" in descriptions["revert_file"]
    assert "json_data" in descriptions["convert_json_to_csv"]
    assert "file_id" in descriptions["convert_csv_to_json"]


def test_execution_plan_validate_rejects_duplicate_orders():
    plan = ExecutionPlan.from_dict(
        {
            "goal": "test",
            "steps": [
                {"order": 1, "tool": "query_file_data", "input": {}, "reason": "a"},
                {"order": 1, "tool": "get_first_n_rows", "input": {}, "reason": "b"},
            ],
        }
    )
    with pytest.raises(InvalidInputError):
        plan.validate()


def test_execution_plan_ordered_steps_sorts_by_order():
    plan = ExecutionPlan.from_dict(
        {
            "goal": "test",
            "steps": [
                {"order": 2, "tool": "get_first_n_rows", "input": {}, "reason": "b"},
                {"order": 1, "tool": "query_file_data", "input": {}, "reason": "a"},
            ],
        }
    )
    assert [step.order for step in plan.ordered_steps()] == [1, 2]
