"""Gap-fill unit specs for ExecuteTaskUseCase — covers rule-parsing branches,
the default-engine fallback, and the array-mapping parse guards that the
baseline suite left uncovered (usecase lines 190-191, 247, 254-258, 263-267,
285, 288). Grounded in execute_task_usecase.py source; mock idiom follows
conftest (sync DummyLogger/DummyMonitor). All collaborators are sync → no
AsyncMock. Duration asserts use >= 0 (§A2 Windows-flaky monotonic guard).
"""
import pytest

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
)
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
)


def _cmd(**inputs) -> ExecuteTaskCommand:
    return ExecuteTaskCommand(task_id="tgap", action="mapping_data", inputs=inputs)


# -- Default engine fallback (usecase 189-191) --------------------------------


class TestDefaultEngineFallback:
    """When no mapping_engine is injected, the use-case imports and builds the
    DefaultMappingEngine itself (source lines 189-191)."""

    def test_no_engine_injected_uses_default(self, logger, monitor):
        # happy: mapping_engine omitted → default engine resolves + maps
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        assert uc.mapping_engine is None
        result = uc.execute(_cmd(
            source_data={"name": "Alice"},
            mapping_rules=[{"source_path": "name", "target_field": "who"}],
        ))
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["mapped_data"] == {"who": "Alice"}
        assert result.duration_ms >= 0


# -- Rule parsing guard branches ----------------------------------------------


class TestRuleParsingGuards:
    def test_direct_rule_without_source_path_skipped(self, logger, monitor):
        # invalid input: DIRECT rule missing source_path → skipped (line 247 continue),
        # so nothing is mapped.
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd(
            source_data={"a": 1},
            mapping_rules=[{"source_path": "", "target_field": "out"}],
        ))
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["mapped_data"] == {}
        assert result.outputs["applied_rules_count"] == 0

    def test_constant_rule_without_source_path_kept(self, logger, monitor):
        # edge: CONSTANT mode is exempt from the empty-source_path skip (line 246
        # condition) — the rule is retained and applied from default_value.
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd(
            source_data={},
            mapping_rules=[{
                "source_path": "",
                "target_field": "fixed",
                "default_value": "K",
                "mapping_mode": "constant",
            }],
        ))
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["mapped_data"] == {"fixed": "K"}

    def test_invalid_field_type_defaults_to_string(self, logger, monitor):
        # invalid input: unknown field_type → warning + default to "string"
        # (usecase lines 253-258). Value coerced as string.
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd(
            source_data={"n": 5},
            mapping_rules=[{"source_path": "n", "target_field": "out", "field_type": "bogus"}],
        ))
        assert result.status == TaskStatus.SUCCESS
        # str coercion of 5 → "5"
        assert result.outputs["mapped_data"] == {"out": "5"}

    def test_invalid_mapping_mode_defaults_to_direct(self, logger, monitor):
        # invalid input: unknown mapping_mode → warning + default to "direct"
        # (usecase lines 261-267). Rule then resolves via DIRECT path.
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd(
            source_data={"n": "hi"},
            mapping_rules=[{"source_path": "n", "target_field": "out", "mapping_mode": "wat"}],
        ))
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["mapped_data"] == {"out": "hi"}


# -- Array-mapping parse guards (usecase 284-285, 287-288) --------------------


class TestArrayMappingParseGuards:
    def test_array_mapping_missing_source_path_skipped(self, logger, monitor):
        # invalid input: array mapping with empty source_array_path is dropped
        # during parse (line 284-285 continue) → no target key produced.
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd(
            source_data={"items": [{"a": 1}]},
            array_mappings=[{
                "source_array_path": "",
                "target_field": "out",
                "item_mappings": [{"source_path": "a", "target_field": "b"}],
            }],
        ))
        assert result.status == TaskStatus.SUCCESS
        assert "out" not in result.outputs["mapped_data"]

    def test_array_mapping_missing_target_field_skipped(self, logger, monitor):
        # invalid input: array mapping with empty target_field is dropped (line 284-285).
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd(
            source_data={"items": [{"a": 1}]},
            array_mappings=[{
                "source_array_path": "items",
                "target_field": "",
                "item_mappings": [{"source_path": "a", "target_field": "b"}],
            }],
        ))
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["mapped_data"] == {}

    def test_array_mapping_invalid_output_mode_defaults_objects(self, logger, monitor):
        # boundary: an unrecognized output_mode is coerced to "objects" during
        # parse (usecase lines 287-288), so items map to dicts, not a flat list.
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd(
            source_data={"items": [{"a": 1}, {"a": 2}]},
            array_mappings=[{
                "source_array_path": "items",
                "target_field": "out",
                "output_mode": "sideways",
                "item_mappings": [{"source_path": "a", "target_field": "v", "field_type": "number"}],
            }],
        ))
        assert result.status == TaskStatus.SUCCESS
        assert result.outputs["mapped_data"]["out"] == [{"v": 1}, {"v": 2}]

    def test_array_mapping_pluck_field_empty_string_becomes_none(self, logger, monitor):
        # edge: pluck_field="" → parsed to None (line 289 `or None`); combined
        # with output_mode="values" the engine then reports the missing-pluck error.
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd(
            source_data={"items": [{"a": 1}]},
            array_mappings=[{
                "source_array_path": "items",
                "target_field": "out",
                "output_mode": "values",
                "pluck_field": "",
            }],
        ))
        assert result.status == TaskStatus.SUCCESS
        assert "out" not in result.outputs["mapped_data"]
        assert len(result.outputs["errors"]) == 1


# -- Duration is non-negative (not > 0) — §A2 Windows monotonic guard ---------


class TestDurationNonNegative:
    def test_success_duration_non_negative(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd(
            source_data={"a": 1},
            mapping_rules=[{"source_path": "a", "target_field": "b"}],
        ))
        assert result.duration_ms >= 0

    def test_error_duration_non_negative(self, logger, monitor):
        # failure path: missing source_data → ERROR branch still records duration.
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        result = uc.execute(_cmd(mapping_rules=[]))
        assert result.status == TaskStatus.ERROR
        assert result.duration_ms >= 0
