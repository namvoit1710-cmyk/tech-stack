"""SA-1813 — nested target paths + null/type-error policies + fail_on_error.

Closes engine gaps G3 (flat-output-only) and G5 (null -> "None" footgun +
errors-buried-as-SUCCESS). Adds three task-level policies whose DEFAULTS derive
from ``strict_mode`` so every pre-existing caller is unchanged:

* ``on_missing``    skip (default lenient) / null / error (default strict)
* ``on_type_error`` error (default) / null / keep
* ``fail_on_error`` any recorded error -> task ERROR (default: SUCCESS)

Grounded in default_mapping_engine.py / execute_task_usecase.py source.
"""
import pytest

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
)
from app.layer1_domain.entities.mapping_rule import ArrayMappingConfig, MappingRule
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase, MAPPING_INPUT_SCHEMA,
)
from app.layer4_frameworks.providers.default_mapping_engine import DefaultMappingEngine


@pytest.fixture
def engine():
    return DefaultMappingEngine()


def _cmd(**inputs):
    return ExecuteTaskCommand(task_id="t-1813", action="mapping_data", inputs=inputs)


# =========================================================================
# Nested target paths (G3)
# =========================================================================


class TestNestedTargetPaths:
    def test_single_level_nested_target(self, engine):
        r = engine.map_data(
            source_data={"full_name": "Alice"},
            mapping_rules=[MappingRule(source_path="full_name", target_field="customer.name")],
            array_mappings=[],
        )
        assert r["mapped_data"] == {"customer": {"name": "Alice"}}

    def test_multi_level_nested_target(self, engine):
        r = engine.map_data(
            source_data={"v": 1},
            mapping_rules=[MappingRule(source_path="v", target_field="a.b.c", field_type="number")],
            array_mappings=[],
        )
        assert r["mapped_data"] == {"a": {"b": {"c": 1}}}

    def test_two_rules_share_a_prefix_merge_into_one_dict(self, engine):
        r = engine.map_data(
            source_data={"x": "X", "y": "Y"},
            mapping_rules=[
                MappingRule(source_path="x", target_field="obj.x"),
                MappingRule(source_path="y", target_field="obj.y"),
            ],
            array_mappings=[],
        )
        assert r["mapped_data"] == {"obj": {"x": "X", "y": "Y"}}

    def test_nested_overwrites_non_dict_intermediate(self, engine):
        # a flat rule writes "obj" as a scalar, then a nested rule needs obj to be
        # a dict — the non-dict intermediate is replaced so the write succeeds.
        r = engine.map_data(
            source_data={"a": "scalar", "b": "deep"},
            mapping_rules=[
                MappingRule(source_path="a", target_field="obj"),
                MappingRule(source_path="b", target_field="obj.inner"),
            ],
            array_mappings=[],
        )
        assert r["mapped_data"] == {"obj": {"inner": "deep"}}

    def test_flat_target_unchanged(self, engine):
        r = engine.map_data(
            source_data={"a": 1},
            mapping_rules=[MappingRule(source_path="a", target_field="b", field_type="number")],
            array_mappings=[],
        )
        assert r["mapped_data"] == {"b": 1}

    def test_nested_target_in_array_item(self, engine):
        r = engine.map_data(
            source_data={"items": [{"n": "A"}, {"n": "B"}]},
            mapping_rules=[],
            array_mappings=[ArrayMappingConfig(
                source_array_path="items", target_field="out",
                item_mappings=[MappingRule(source_path="n", target_field="meta.name")],
            )],
        )
        assert r["mapped_data"]["out"] == [{"meta": {"name": "A"}}, {"meta": {"name": "B"}}]

    def test_nested_target_for_array_result(self, engine):
        r = engine.map_data(
            source_data={"items": [{"a": 1}]},
            mapping_rules=[],
            array_mappings=[ArrayMappingConfig(
                source_array_path="items", target_field="result.rows", output_mode="values",
                pluck_field="a",
            )],
        )
        assert r["mapped_data"] == {"result": {"rows": [1]}}


# =========================================================================
# Null footgun fix (G5) + on_missing policy
# =========================================================================


class TestOnMissing:
    def test_null_no_default_lenient_skips(self, engine):
        r = engine.map_data(
            source_data={"a": None},
            mapping_rules=[MappingRule(source_path="a", target_field="out")],
            array_mappings=[],
        )
        assert "out" not in r["mapped_data"]         # NOT the literal "None"
        assert "a" in r["skipped_rules"]

    def test_null_no_default_strict_errors(self, engine):
        r = engine.map_data(
            source_data={"a": None},
            mapping_rules=[MappingRule(source_path="a", target_field="out")],
            array_mappings=[], strict_mode=True,
        )
        assert "out" not in r["mapped_data"]
        assert len(r["errors"]) == 1

    def test_missing_on_missing_null_writes_null(self, engine):
        r = engine.map_data(
            source_data={"a": 1},
            mapping_rules=[MappingRule(source_path="nope", target_field="out")],
            array_mappings=[], on_missing="null",
        )
        assert r["mapped_data"] == {"out": None}
        assert r["applied_rules_count"] == 1

    def test_missing_on_missing_error_records(self, engine):
        r = engine.map_data(
            source_data={"a": 1},
            mapping_rules=[MappingRule(source_path="nope", target_field="out")],
            array_mappings=[], on_missing="error",
        )
        assert "out" not in r["mapped_data"]
        assert len(r["errors"]) == 1

    def test_null_value_on_missing_null_writes_null(self, engine):
        # explicit: a null source with on_missing=null writes null (intentional null).
        r = engine.map_data(
            source_data={"a": None},
            mapping_rules=[MappingRule(source_path="a", target_field="out")],
            array_mappings=[], on_missing="null",
        )
        assert r["mapped_data"] == {"out": None}

    def test_on_missing_default_derives_from_strict(self, engine):
        # no explicit on_missing → strict True behaves like error
        r = engine.map_data(
            source_data={}, mapping_rules=[MappingRule(source_path="x", target_field="o")],
            array_mappings=[], strict_mode=True,
        )
        assert len(r["errors"]) == 1


# =========================================================================
# on_type_error policy
# =========================================================================


class TestOnTypeError:
    def test_default_error_records_and_skips(self, engine):
        r = engine.map_data(
            source_data={"v": "abc"},
            mapping_rules=[MappingRule(source_path="v", target_field="n", field_type="number")],
            array_mappings=[],
        )
        assert "n" not in r["mapped_data"]
        assert len(r["errors"]) == 1

    def test_null_writes_null_on_coercion_failure(self, engine):
        r = engine.map_data(
            source_data={"v": "abc"},
            mapping_rules=[MappingRule(source_path="v", target_field="n", field_type="number")],
            array_mappings=[], on_type_error="null",
        )
        assert r["mapped_data"] == {"n": None}
        assert r["errors"] == []

    def test_keep_writes_raw_value_on_coercion_failure(self, engine):
        r = engine.map_data(
            source_data={"v": "abc"},
            mapping_rules=[MappingRule(source_path="v", target_field="n", field_type="number")],
            array_mappings=[], on_type_error="keep",
        )
        assert r["mapped_data"] == {"n": "abc"}        # raw, uncoerced
        assert r["errors"] == []


# =========================================================================
# fail_on_error (use case level)
# =========================================================================


class TestFailOnError:
    def test_fail_on_error_true_with_errors_returns_error_status(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        res = uc.execute(_cmd(
            source_data={"v": "abc"},
            mapping_rules=[{"source_path": "v", "target_field": "n", "field_type": "number"}],
            fail_on_error=True,
        ))
        assert res.status == TaskStatus.ERROR
        assert "mapping error" in (res.error or "")

    def test_fail_on_error_true_without_errors_succeeds(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        res = uc.execute(_cmd(
            source_data={"v": "5"},
            mapping_rules=[{"source_path": "v", "target_field": "n", "field_type": "number"}],
            fail_on_error=True,
        ))
        assert res.status == TaskStatus.SUCCESS
        assert res.outputs["mapped_data"] == {"n": 5}

    def test_default_fail_on_error_false_succeeds_with_errors(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        res = uc.execute(_cmd(
            source_data={"v": "abc"},
            mapping_rules=[{"source_path": "v", "target_field": "n", "field_type": "number"}],
        ))
        assert res.status == TaskStatus.SUCCESS
        assert len(res.outputs["errors"]) == 1


# =========================================================================
# Use-case policy passthrough + schema exposure
# =========================================================================


class TestUseCasePolicyAndSchema:
    def test_policy_fields_in_input_schema(self):
        keys = {f["key"] for f in MAPPING_INPUT_SCHEMA}
        assert {"on_missing", "on_type_error", "fail_on_error"} <= keys

    def test_on_missing_null_end_to_end_via_use_case(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        res = uc.execute(_cmd(
            source_data={"a": 1},
            mapping_rules=[{"source_path": "missing", "target_field": "out"}],
            on_missing="null",
        ))
        assert res.status == TaskStatus.SUCCESS
        assert res.outputs["mapped_data"] == {"out": None}

    def test_nested_target_end_to_end_via_use_case(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        res = uc.execute(_cmd(
            source_data={"full_name": "Bob"},
            mapping_rules=[{"source_path": "full_name", "target_field": "customer.name"}],
        ))
        assert res.outputs["mapped_data"] == {"customer": {"name": "Bob"}}
