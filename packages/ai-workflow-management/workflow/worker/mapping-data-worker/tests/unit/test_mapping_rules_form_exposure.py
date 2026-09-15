"""SA-1812 — the flagship field-to-field mapping (``mapping_rules``) is now a
first-class form control (closes G1), and the dead ``ARRAY_MAP`` mode is gone.

Before SA-1812 the engine read top-level ``mapping_rules`` but it was ABSENT
from ``MAPPING_INPUT_SCHEMA``, so a builder could only reach it via raw JSON on
the node's input binding. These specs pin:

1. schema contract — ``mapping_rules`` is exposed as an array table with the
   right columns and the right (post-ARRAY_MAP) mode options;
2. palette node — the ``mapping_data`` NodeTypeDefinition surfaces it (and the
   multi-type foundation ``mapping_data`` + ``array_transform`` is intact);
3. end-to-end — values collected by that form field actually map, through both
   the use case and the real node handler adapter.

Grounded in execute_task_usecase.py / node_types.py / mapping_mode.py source.
Sync collaborators (conftest logger/monitor) → no AsyncMock.
"""
import json

import pytest

from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskCommand,
)
from app.layer1_domain.value_objects.mapping_field_type import MappingFieldType
from app.layer1_domain.value_objects.mapping_mode import MappingMode
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
    MAPPING_INPUT_SCHEMA,
)
from app.layer4_frameworks.config import settings
from app import node_types as nt


def _cmd(**inputs) -> ExecuteTaskCommand:
    return ExecuteTaskCommand(task_id="t-1812", action="mapping_data", inputs=inputs)


def _field(schema, key):
    return next((f for f in schema if f.get("key") == key), None)


# =========================================================================
# 1. Schema contract — mapping_rules is exposed (the P0; would FAIL pre-1812)
# =========================================================================


class TestMappingRulesInSchema:
    def test_mapping_rules_present_in_input_schema(self):
        keys = [f["key"] for f in MAPPING_INPUT_SCHEMA]
        assert "mapping_rules" in keys, (
            "SA-1812 regression: field-mapping table not exposed in the form schema"
        )

    def test_all_expected_top_level_fields_present(self):
        keys = {f["key"] for f in MAPPING_INPUT_SCHEMA}
        assert {"source_data", "mapping_rules", "strict_mode", "array_mappings"} <= keys

    def test_mapping_rules_is_an_array_field(self):
        field = _field(MAPPING_INPUT_SCHEMA, "mapping_rules")
        assert field is not None
        assert field.get("outputType") == "array"

    def test_mapping_rules_exposes_all_columns(self):
        field = _field(MAPPING_INPUT_SCHEMA, "mapping_rules")
        blob = json.dumps(field)
        for column in ("source_path", "target_field", "field_type",
                       "default_value", "mapping_mode"):
            assert column in blob, f"column {column!r} missing from mapping_rules table"

    def test_field_type_column_offers_all_five_types(self):
        blob = json.dumps(_field(MAPPING_INPUT_SCHEMA, "mapping_rules"))
        for t in (MappingFieldType.STRING, MappingFieldType.NUMBER,
                  MappingFieldType.BOOLEAN, MappingFieldType.OBJECT,
                  MappingFieldType.ARRAY):
            assert f'"{t.value}"' in blob

    def test_mapping_mode_column_offers_direct_and_constant_only(self):
        blob = json.dumps(_field(MAPPING_INPUT_SCHEMA, "mapping_rules"))
        assert '"direct"' in blob
        assert '"constant"' in blob

    def test_mapping_mode_column_has_no_array_map_option(self):
        # SA-1812: dead ARRAY_MAP must not leak into the form.
        blob = json.dumps(_field(MAPPING_INPUT_SCHEMA, "mapping_rules"))
        assert "array_map" not in blob

    def test_mapping_rules_ordered_before_array_mappings(self):
        keys = [f["key"] for f in MAPPING_INPUT_SCHEMA]
        assert keys.index("mapping_rules") < keys.index("array_mappings"), (
            "field mapping should be the primary control, above array mappings"
        )


# =========================================================================
# 2. Palette node — the mapping_data node exposes it; foundation intact
# =========================================================================


class TestMultiTypeNodeExposure:
    def test_two_node_types_registered(self):
        wts = {d.worker_type for d in nt.NODE_TYPES}
        assert settings.WORKER_TYPE in wts          # "mapping_data"
        assert "array_transform" in wts             # SA-1078 sibling intact

    def test_mapping_node_input_schema_exposes_mapping_rules(self):
        mapping_def = next(d for d in nt.NODE_TYPES if d.worker_type == settings.WORKER_TYPE)
        keys = [f["key"] for f in mapping_def.input_schema]
        assert "mapping_rules" in keys

    def test_array_transform_node_untouched(self):
        atx = next(d for d in nt.NODE_TYPES if d.worker_type == "array_transform")
        atx_keys = [f["key"] for f in atx.input_schema]
        # the array-merge node has its own schema — mapping_rules must NOT bleed in
        assert "mapping_rules" not in atx_keys
        assert "array1" in atx_keys and "array2" in atx_keys


# =========================================================================
# 3. End-to-end — values the form collects actually map (use case path)
# =========================================================================


class TestMappingRulesEndToEnd:
    def test_direct_field_mapping(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        res = uc.execute(_cmd(
            source_data={"full_name": "Alice", "amount": "42"},
            mapping_rules=[
                {"source_path": "full_name", "target_field": "name"},
                {"source_path": "amount", "target_field": "total", "field_type": "number"},
            ],
        ))
        assert res.status.value == "success"
        assert res.outputs["mapped_data"] == {"name": "Alice", "total": 42}
        assert res.outputs["applied_rules_count"] == 2

    def test_constant_mode(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        res = uc.execute(_cmd(
            source_data={},
            mapping_rules=[{
                "target_field": "channel", "default_value": "web",
                "mapping_mode": "constant",
            }],
        ))
        assert res.outputs["mapped_data"] == {"channel": "web"}

    def test_nested_source_path_and_array_index(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        res = uc.execute(_cmd(
            source_data={"response_body": {"data": {"items": [{"name": "first"}]}}},
            mapping_rules=[
                {"source_path": "response_body.data.items[0].name", "target_field": "first_name"},
            ],
        ))
        assert res.outputs["mapped_data"] == {"first_name": "first"}

    def test_missing_source_lenient_skips(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        res = uc.execute(_cmd(
            source_data={"a": 1},
            mapping_rules=[{"source_path": "does_not_exist", "target_field": "out"}],
            strict_mode=False,
        ))
        assert "out" not in res.outputs["mapped_data"]
        assert "does_not_exist" in res.outputs["skipped_rules"]

    def test_missing_source_strict_errors(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        res = uc.execute(_cmd(
            source_data={"a": 1},
            mapping_rules=[{"source_path": "does_not_exist", "target_field": "out"}],
            strict_mode=True,
        ))
        assert res.outputs["mapped_data"] == {}
        assert len(res.outputs["errors"]) == 1

    def test_mapping_rules_and_array_mappings_coexist(self, logger, monitor):
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        res = uc.execute(_cmd(
            source_data={"who": "Bob", "items": [{"a": 1}, {"a": 2}]},
            mapping_rules=[{"source_path": "who", "target_field": "name"}],
            array_mappings=[{
                "source_array_path": "items", "target_field": "vals",
                "output_mode": "values", "pluck_field": "a",
            }],
        ))
        assert res.outputs["mapped_data"]["name"] == "Bob"
        assert res.outputs["mapped_data"]["vals"] == [1, 2]

    def test_legacy_array_map_mode_now_defaults_to_direct(self, logger, monitor):
        # SA-1812 back-compat: a saved rule carrying the removed "array_map" mode
        # must not crash — it falls back to DIRECT (warning) and still maps.
        uc = ExecuteTaskUseCase(logger=logger, monitor=monitor)
        res = uc.execute(_cmd(
            source_data={"v": "x"},
            mapping_rules=[{"source_path": "v", "target_field": "out", "mapping_mode": "array_map"}],
        ))
        assert res.status.value == "success"
        assert res.outputs["mapped_data"] == {"out": "x"}


# =========================================================================
# 4. Real node handler adapter (the exact path the palette node dispatches)
# =========================================================================


class TestNodeHandlerPath:
    def test_mapping_handler_maps_via_mapping_rules(self):
        out = nt._mapping_handler(
            {"source_data": {"a": 1}, "mapping_rules": [{"source_path": "a", "target_field": "b", "field_type": "number"}]},
            {},
        )
        assert out["mapped_data"] == {"b": 1}
        assert out["applied_rules_count"] == 1

    def test_mapping_handler_raises_on_error_status(self):
        # missing source_data → use case returns ERROR → handler raises (SA-1078
        # status contract: worker-level failure surfaces as an exception).
        with pytest.raises(RuntimeError):
            nt._mapping_handler({"mapping_rules": []}, {})
