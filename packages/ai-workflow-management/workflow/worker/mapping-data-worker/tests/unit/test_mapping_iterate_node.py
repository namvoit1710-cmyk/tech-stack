"""SA-1814 — dedicated Map-Array / For-Each node (`mapping_iterate`) + the
index-aligned `values` fix (G4).

Before: `values` mode dropped an item whose pluck field was missing, so the
output list became SHORTER than the source (index skew for downstream
zip-by-index). Now a missing pluck emits `null` at that index — length is
preserved. Also registers a dedicated `mapping_iterate` palette node whose
handler reuses the existing use case (parity with the mapping_data node's
array_mappings).
"""
import pytest

from app.layer1_domain.entities.mapping_rule import ArrayMappingConfig, MappingRule
from app.layer4_frameworks.providers.default_mapping_engine import DefaultMappingEngine
from app import node_types as nt


@pytest.fixture
def engine():
    return DefaultMappingEngine()


# =========================================================================
# G4 — values mode is index-aligned (engine)
# =========================================================================


class TestValuesIndexAligned:
    def test_missing_pluck_emits_null_not_shorter_lenient(self, engine):
        r = engine.map_data(
            source_data={"items": [{"a": 1}, {"b": 2}, {"a": 3}]},
            mapping_rules=[],
            array_mappings=[ArrayMappingConfig(
                source_array_path="items", target_field="vals",
                output_mode="values", pluck_field="a",
            )],
        )
        # length matches source (3), missing 'a' at index 1 -> null (NOT dropped)
        assert r["mapped_data"]["vals"] == [1, None, 3]

    def test_missing_pluck_strict_records_error_and_keeps_alignment(self, engine):
        r = engine.map_data(
            source_data={"items": [{"a": 1}, {"b": 2}]},
            mapping_rules=[],
            array_mappings=[ArrayMappingConfig(
                source_array_path="items", target_field="vals",
                output_mode="values", pluck_field="a",
            )],
            strict_mode=True,
        )
        assert r["mapped_data"]["vals"] == [1, None]   # still length 2
        assert len(r["errors"]) == 1
        assert "items[1]" in r["errors"][0]

    def test_all_present_unchanged(self, engine):
        r = engine.map_data(
            source_data={"items": [{"a": 1}, {"a": 2}]},
            mapping_rules=[],
            array_mappings=[ArrayMappingConfig(
                source_array_path="items", target_field="vals",
                output_mode="values", pluck_field="a",
            )],
        )
        assert r["mapped_data"]["vals"] == [1, 2]


# =========================================================================
# mapping_iterate node registration + schema
# =========================================================================


class TestIterateNodeRegistration:
    def test_node_types_include_iterate(self):
        wts = [d.worker_type for d in nt.NODE_TYPES]
        # order-stable prefix; mapping_aggregate (SA-1815) appended after
        assert wts[:3] == ["mapping_data", "array_transform", "mapping_iterate"]
        assert "mapping_iterate" in wts

    def test_iterate_input_schema_keys(self):
        keys = [f["key"] for f in nt.ITERATE_NODE_DEF.input_schema]
        assert {"source_data", "source_array_path", "output_mode", "pluck_field",
                "item_mappings", "strict_mode"} <= set(keys)

    def test_iterate_output_has_result(self):
        keys = [f["key"] for f in nt.ITERATE_NODE_DEF.output_schema]
        assert "result" in keys and "errors" in keys


# =========================================================================
# _iterate_handler (the real dispatch path)
# =========================================================================


class TestIterateHandler:
    def test_objects_mode_maps_each_item(self):
        out = nt._iterate_handler({
            "source_data": {"items": [{"full_name": "A"}, {"full_name": "B"}]},
            "source_array_path": "items",
            "output_mode": "objects",
            "item_mappings": [{"source_path": "full_name", "target_field": "name"}],
        }, {})
        assert out["result"] == [{"name": "A"}, {"name": "B"}]
        assert out["errors"] == []

    def test_values_mode_index_aligned_via_handler(self):
        out = nt._iterate_handler({
            "source_data": {"items": [{"a": 1}, {"b": 2}, {"a": 3}]},
            "source_array_path": "items",
            "output_mode": "values",
            "pluck_field": "a",
        }, {})
        assert out["result"] == [1, None, 3]

    def test_nested_item_target(self):
        out = nt._iterate_handler({
            "source_data": {"items": [{"n": "A"}]},
            "source_array_path": "items",
            "output_mode": "objects",
            "item_mappings": [{"source_path": "n", "target_field": "meta.name"}],
        }, {})
        assert out["result"] == [{"meta": {"name": "A"}}]

    def test_empty_or_missing_array(self):
        out = nt._iterate_handler({
            "source_data": {"other": 1},
            "source_array_path": "items",
            "output_mode": "objects",
            "item_mappings": [{"source_path": "n", "target_field": "name"}],
        }, {})
        # missing source array -> nothing mapped (result absent -> handler defaults [])
        assert out["result"] == []

    def test_strict_missing_pluck_reports_error_but_succeeds(self):
        # strict_mode records errors but does NOT fail the task (only fail_on_error
        # would); the handler returns the aligned result + the errors.
        out = nt._iterate_handler({
            "source_data": {"items": [{"a": 1}, {"b": 2}]},
            "source_array_path": "items",
            "output_mode": "values",
            "pluck_field": "a",
            "strict_mode": True,
        }, {})
        assert out["result"] == [1, None]
        assert len(out["errors"]) == 1
