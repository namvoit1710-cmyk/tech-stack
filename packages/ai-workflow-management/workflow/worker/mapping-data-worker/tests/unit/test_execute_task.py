import json

import pytest

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
)
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
    MAPPING_INPUT_SCHEMA,
    MAPPING_OUTPUT_SCHEMA,
)
from app.layer4_frameworks.providers.default_mapping_engine import DefaultMappingEngine


@pytest.fixture
def engine():
    return DefaultMappingEngine()


class TestSimpleMappingSuccess:
    def test_basic_mapping(self, logger, monitor, engine):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, mapping_engine=engine)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t1",
            action="mapping_data",
            inputs={
                "source_data": {"name": "Alice", "age": 30},
                "mapping_rules": [
                    {"source_path": "name", "target_field": "user_name"},
                    {"source_path": "age", "target_field": "user_age", "field_type": "number"},
                ],
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["mapped_data"] == {"user_name": "Alice", "user_age": 30}
        assert result.outputs["applied_rules_count"] == 2
        assert result.outputs["skipped_rules"] == []
        assert result.outputs["errors"] == []
        assert result.duration_ms > 0

    def test_mapping_with_all_features(self, logger, monitor, engine):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, mapping_engine=engine)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t2",
            action="mapping_data",
            inputs={
                "source_data": {
                    "api_status": "200",
                    "items": [
                        {"product": "Widget", "cost": "9.99"},
                        {"product": "Gadget", "cost": "19.99"},
                    ],
                },
                "mapping_rules": [
                    {"source_path": "api_status", "target_field": "status_code", "field_type": "number"},
                    {"source_path": "", "target_field": "source", "default_value": "external_api", "mapping_mode": "constant"},
                ],
                "array_mappings": [
                    {
                        "source_array_path": "items",
                        "target_field": "products",
                        "item_mappings": [
                            {"source_path": "product", "target_field": "name"},
                            {"source_path": "cost", "target_field": "price", "field_type": "number"},
                        ],
                    },
                ],
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        data = result.outputs["mapped_data"]
        assert data["status_code"] == 200
        assert data["source"] == "external_api"
        assert data["products"] == [
            {"name": "Widget", "price": 9.99},
            {"name": "Gadget", "price": 19.99},
        ]


class TestSourceDataParsing:
    def test_source_data_as_json_string(self, logger, monitor, engine):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, mapping_engine=engine)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t3",
            action="mapping_data",
            inputs={
                "source_data": '{"key": "value"}',
                "mapping_rules": [
                    {"source_path": "key", "target_field": "out"},
                ],
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["mapped_data"] == {"out": "value"}

    def test_invalid_source_data_string(self, logger, monitor, engine):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, mapping_engine=engine)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t4",
            action="mapping_data",
            inputs={
                "source_data": "not valid json",
                "mapping_rules": [],
            },
        ))

        assert result.status == TaskStatus.ERROR
        assert result.error is not None

    def test_missing_source_data(self, logger, monitor, engine):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, mapping_engine=engine)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t5",
            action="mapping_data",
            inputs={
                "mapping_rules": [],
            },
        ))

        assert result.status == TaskStatus.ERROR
        assert "'source_data' is required" in result.error

    def test_source_data_not_object(self, logger, monitor, engine):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, mapping_engine=engine)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t6",
            action="mapping_data",
            inputs={
                "source_data": [1, 2, 3],
                "mapping_rules": [],
            },
        ))

        assert result.status == TaskStatus.ERROR
        assert "object" in result.error.lower()


class TestStrictMode:
    def test_strict_mode_missing_field(self, logger, monitor, engine):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, mapping_engine=engine)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t7",
            action="mapping_data",
            inputs={
                "source_data": {"a": 1},
                "strict_mode": True,
                "mapping_rules": [
                    {"source_path": "missing", "target_field": "out"},
                ],
            },
        ))

        assert result.status == TaskStatus.SUCCESS
        assert len(result.outputs["errors"]) == 1


class TestMetrics:
    def test_success_metrics_tracked(self, logger, monitor, engine):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, mapping_engine=engine)
        uc.execute(ExecuteTaskCommand(
            task_id="t8",
            action="mapping_data",
            inputs={
                "source_data": {"a": 1},
                "mapping_rules": [{"source_path": "a", "target_field": "b"}],
            },
        ))

        metric_names = [m[0] for m in monitor.tracked]
        assert "mapping_data_task_requests" in metric_names
        assert "mapping_data_task_duration_ms" in metric_names
        assert "mapping_data_task_success" in metric_names

    def test_error_metrics_tracked(self, logger, monitor, engine):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, mapping_engine=engine)
        uc.execute(ExecuteTaskCommand(
            task_id="t9",
            action="mapping_data",
            inputs={},
        ))

        metric_names = [m[0] for m in monitor.tracked]
        assert "mapping_data_task_requests" in metric_names
        assert "mapping_data_task_error" in metric_names


class TestSchemas:
    def test_input_schema_is_list(self):
        assert isinstance(MAPPING_INPUT_SCHEMA, list)
        assert len(MAPPING_INPUT_SCHEMA) > 0

    def test_output_schema_is_list(self):
        assert isinstance(MAPPING_OUTPUT_SCHEMA, list)
        assert len(MAPPING_OUTPUT_SCHEMA) > 0

    def test_input_schema_required_keys(self):
        keys = [field["key"] for field in MAPPING_INPUT_SCHEMA]
        assert "source_data" in keys
        assert "strict_mode" in keys
        assert "array_mappings" in keys

    def test_output_schema_required_keys(self):
        keys = [field["key"] for field in MAPPING_OUTPUT_SCHEMA]
        assert "mapped_data" in keys
        assert "applied_rules_count" in keys
        assert "skipped_rules" in keys
        assert "errors" in keys


class TestInvalidRulesParsing:
    def test_rule_without_target_field_skipped(self, logger, monitor, engine):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, mapping_engine=engine)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t10",
            action="mapping_data",
            inputs={
                "source_data": {"a": 1},
                "mapping_rules": [
                    {"source_path": "a"},
                ],
            },
        ))
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["mapped_data"] == {}

    def test_empty_mapping_rules(self, logger, monitor, engine):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor, mapping_engine=engine)
        result = uc.execute(ExecuteTaskCommand(
            task_id="t11",
            action="mapping_data",
            inputs={
                "source_data": {"a": 1},
                "mapping_rules": [],
                "array_mappings": [],
            },
        ))
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["mapped_data"] == {}
        assert result.outputs["applied_rules_count"] == 0
