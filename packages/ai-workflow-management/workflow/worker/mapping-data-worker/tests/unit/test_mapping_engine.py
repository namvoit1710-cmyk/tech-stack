import pytest

from app.layer1_domain.entities.mapping_rule import ArrayMappingConfig, MappingRule
from app.layer4_frameworks.providers.default_mapping_engine import DefaultMappingEngine


@pytest.fixture
def engine():
    return DefaultMappingEngine()


# -- Simple Field Mapping ------------------------------------------------------


class TestSimpleFieldMapping:
    def test_direct_flat_field(self, engine):
        result = engine.map_data(
            source_data={"name": "Alice"},
            mapping_rules=[MappingRule(source_path="name", target_field="user_name")],
            array_mappings=[],
        )
        assert result["mapped_data"] == {"user_name": "Alice"}
        assert result["applied_rules_count"] == 1
        assert result["skipped_rules"] == []
        assert result["errors"] == []

    def test_direct_nested_field(self, engine):
        result = engine.map_data(
            source_data={"data": {"user": {"name": "Bob"}}},
            mapping_rules=[MappingRule(source_path="data.user.name", target_field="full_name")],
            array_mappings=[],
        )
        assert result["mapped_data"] == {"full_name": "Bob"}

    def test_direct_array_index(self, engine):
        result = engine.map_data(
            source_data={"items": ["a", "b", "c"]},
            mapping_rules=[MappingRule(source_path="items[1]", target_field="second")],
            array_mappings=[],
        )
        assert result["mapped_data"] == {"second": "b"}

    def test_nested_array_index(self, engine):
        result = engine.map_data(
            source_data={"data": {"items": [{"id": 1}, {"id": 2}]}},
            mapping_rules=[MappingRule(source_path="data.items[1].id", target_field="item_id", field_type="number")],
            array_mappings=[],
        )
        assert result["mapped_data"] == {"item_id": 2}

    def test_multiple_rules(self, engine):
        result = engine.map_data(
            source_data={"first": "Alice", "last": "Smith", "age": 30},
            mapping_rules=[
                MappingRule(source_path="first", target_field="first_name"),
                MappingRule(source_path="last", target_field="last_name"),
                MappingRule(source_path="age", target_field="user_age", field_type="number"),
            ],
            array_mappings=[],
        )
        assert result["mapped_data"] == {"first_name": "Alice", "last_name": "Smith", "user_age": 30}
        assert result["applied_rules_count"] == 3

    def test_deeply_nested_path(self, engine):
        result = engine.map_data(
            source_data={"a": {"b": {"c": {"d": {"e": "deep"}}}}},
            mapping_rules=[MappingRule(source_path="a.b.c.d.e", target_field="value")],
            array_mappings=[],
        )
        assert result["mapped_data"] == {"value": "deep"}


# -- Type Coercion ------------------------------------------------------------


class TestTypeCoercion:
    def test_string_to_number_int(self, engine):
        result = engine.map_data(
            source_data={"val": "42"},
            mapping_rules=[MappingRule(source_path="val", target_field="num", field_type="number")],
            array_mappings=[],
        )
        assert result["mapped_data"]["num"] == 42

    def test_string_to_number_float(self, engine):
        result = engine.map_data(
            source_data={"val": "3.14"},
            mapping_rules=[MappingRule(source_path="val", target_field="num", field_type="number")],
            array_mappings=[],
        )
        assert result["mapped_data"]["num"] == 3.14

    def test_string_to_boolean_true(self, engine):
        for val in ["true", "1", "yes"]:
            result = engine.map_data(
                source_data={"val": val},
                mapping_rules=[MappingRule(source_path="val", target_field="flag", field_type="boolean")],
                array_mappings=[],
            )
            assert result["mapped_data"]["flag"] is True, f"Failed for '{val}'"

    def test_string_to_boolean_false(self, engine):
        for val in ["false", "0", "no"]:
            result = engine.map_data(
                source_data={"val": val},
                mapping_rules=[MappingRule(source_path="val", target_field="flag", field_type="boolean")],
                array_mappings=[],
            )
            assert result["mapped_data"]["flag"] is False, f"Failed for '{val}'"

    def test_number_to_string(self, engine):
        result = engine.map_data(
            source_data={"val": 42},
            mapping_rules=[MappingRule(source_path="val", target_field="text", field_type="string")],
            array_mappings=[],
        )
        assert result["mapped_data"]["text"] == "42"

    def test_dict_to_json_string(self, engine):
        result = engine.map_data(
            source_data={"val": {"a": 1}},
            mapping_rules=[MappingRule(source_path="val", target_field="text", field_type="string")],
            array_mappings=[],
        )
        assert result["mapped_data"]["text"] == '{"a": 1}'

    def test_json_string_to_object(self, engine):
        result = engine.map_data(
            source_data={"val": '{"a": 1}'},
            mapping_rules=[MappingRule(source_path="val", target_field="obj", field_type="object")],
            array_mappings=[],
        )
        assert result["mapped_data"]["obj"] == {"a": 1}

    def test_json_string_to_array(self, engine):
        result = engine.map_data(
            source_data={"val": "[1, 2, 3]"},
            mapping_rules=[MappingRule(source_path="val", target_field="arr", field_type="array")],
            array_mappings=[],
        )
        assert result["mapped_data"]["arr"] == [1, 2, 3]

    def test_invalid_coercion_reports_error(self, engine):
        result = engine.map_data(
            source_data={"val": "not_a_number"},
            mapping_rules=[MappingRule(source_path="val", target_field="num", field_type="number")],
            array_mappings=[],
        )
        assert result["applied_rules_count"] == 0
        assert len(result["errors"]) == 1
        assert "coerce" in result["errors"][0].lower() or "number" in result["errors"][0].lower()

    def test_bool_passthrough(self, engine):
        result = engine.map_data(
            source_data={"val": True},
            mapping_rules=[MappingRule(source_path="val", target_field="flag", field_type="boolean")],
            array_mappings=[],
        )
        assert result["mapped_data"]["flag"] is True

    def test_number_passthrough(self, engine):
        result = engine.map_data(
            source_data={"val": 99},
            mapping_rules=[MappingRule(source_path="val", target_field="num", field_type="number")],
            array_mappings=[],
        )
        assert result["mapped_data"]["num"] == 99


# -- Default Values ------------------------------------------------------------


class TestDefaultValues:
    def test_default_on_missing_field(self, engine):
        result = engine.map_data(
            source_data={},
            mapping_rules=[MappingRule(source_path="missing", target_field="out", default_value="fallback")],
            array_mappings=[],
        )
        assert result["mapped_data"] == {"out": "fallback"}
        assert result["applied_rules_count"] == 1

    def test_default_on_null_field(self, engine):
        result = engine.map_data(
            source_data={"x": None},
            mapping_rules=[MappingRule(source_path="x", target_field="out", default_value="fallback")],
            array_mappings=[],
        )
        assert result["mapped_data"] == {"out": "fallback"}

    def test_no_default_missing_field_lenient(self, engine):
        result = engine.map_data(
            source_data={},
            mapping_rules=[MappingRule(source_path="missing", target_field="out")],
            array_mappings=[],
            strict_mode=False,
        )
        assert "out" not in result["mapped_data"]
        assert result["skipped_rules"] == ["missing"]

    def test_no_default_missing_field_strict(self, engine):
        result = engine.map_data(
            source_data={},
            mapping_rules=[MappingRule(source_path="missing", target_field="out")],
            array_mappings=[],
            strict_mode=True,
        )
        assert "out" not in result["mapped_data"]
        assert len(result["errors"]) == 1
        assert "missing" in result["errors"][0].lower()


# -- Constant Mode -------------------------------------------------------------


class TestConstantMode:
    def test_constant_value(self, engine):
        result = engine.map_data(
            source_data={"irrelevant": "data"},
            mapping_rules=[MappingRule(
                source_path="",
                target_field="fixed_key",
                default_value="fixed_value",
                mapping_mode="constant",
            )],
            array_mappings=[],
        )
        assert result["mapped_data"] == {"fixed_key": "fixed_value"}
        assert result["applied_rules_count"] == 1

    def test_constant_with_type_coercion(self, engine):
        result = engine.map_data(
            source_data={},
            mapping_rules=[MappingRule(
                source_path="",
                target_field="count",
                default_value="42",
                mapping_mode="constant",
                field_type="number",
            )],
            array_mappings=[],
        )
        assert result["mapped_data"] == {"count": 42}

    def test_constant_no_value_missing(self, engine):
        result = engine.map_data(
            source_data={},
            mapping_rules=[MappingRule(
                source_path="",
                target_field="out",
                mapping_mode="constant",
            )],
            array_mappings=[],
            strict_mode=False,
        )
        assert "out" not in result["mapped_data"]


# -- Array Mapping -------------------------------------------------------------


class TestArrayMapping:
    def test_basic_array_mapping(self, engine):
        result = engine.map_data(
            source_data={
                "users": [
                    {"first": "Alice", "last": "Smith"},
                    {"first": "Bob", "last": "Jones"},
                ],
            },
            mapping_rules=[],
            array_mappings=[
                ArrayMappingConfig(
                    source_array_path="users",
                    target_field="mapped_users",
                    item_mappings=[
                        MappingRule(source_path="first", target_field="name"),
                        MappingRule(source_path="last", target_field="surname"),
                    ],
                ),
            ],
        )
        assert result["mapped_data"]["mapped_users"] == [
            {"name": "Alice", "surname": "Smith"},
            {"name": "Bob", "surname": "Jones"},
        ]
        assert result["applied_rules_count"] == 1

    def test_array_mapping_with_type_coercion(self, engine):
        result = engine.map_data(
            source_data={
                "items": [
                    {"name": "Item A", "price": "10.5"},
                    {"name": "Item B", "price": "20"},
                ],
            },
            mapping_rules=[],
            array_mappings=[
                ArrayMappingConfig(
                    source_array_path="items",
                    target_field="products",
                    item_mappings=[
                        MappingRule(source_path="name", target_field="label"),
                        MappingRule(source_path="price", target_field="amount", field_type="number"),
                    ],
                ),
            ],
        )
        products = result["mapped_data"]["products"]
        assert products[0]["amount"] == 10.5
        assert products[1]["amount"] == 20

    def test_array_mapping_with_defaults(self, engine):
        result = engine.map_data(
            source_data={
                "items": [
                    {"name": "A"},
                    {"name": "B", "color": "red"},
                ],
            },
            mapping_rules=[],
            array_mappings=[
                ArrayMappingConfig(
                    source_array_path="items",
                    target_field="out",
                    item_mappings=[
                        MappingRule(source_path="name", target_field="label"),
                        MappingRule(source_path="color", target_field="color", default_value="blue"),
                    ],
                ),
            ],
        )
        items = result["mapped_data"]["out"]
        assert items[0] == {"label": "A", "color": "blue"}
        assert items[1] == {"label": "B", "color": "red"}

    def test_array_mapping_source_not_array(self, engine):
        result = engine.map_data(
            source_data={"items": "not_an_array"},
            mapping_rules=[],
            array_mappings=[
                ArrayMappingConfig(
                    source_array_path="items",
                    target_field="out",
                    item_mappings=[MappingRule(source_path="x", target_field="y")],
                ),
            ],
        )
        assert "out" not in result["mapped_data"]
        assert len(result["errors"]) == 1
        assert "not an array" in result["errors"][0].lower()

    def test_nested_array_path(self, engine):
        result = engine.map_data(
            source_data={"response_body": {"data": {"customers": [{"id": 1}, {"id": 2}]}}},
            mapping_rules=[],
            array_mappings=[
                ArrayMappingConfig(
                    source_array_path="response_body.data.customers",
                    target_field="ids",
                    item_mappings=[MappingRule(source_path="id", target_field="customer_id", field_type="number")],
                ),
            ],
        )
        assert result["mapped_data"]["ids"] == [{"customer_id": 1}, {"customer_id": 2}]

    def test_missing_array_lenient(self, engine):
        result = engine.map_data(
            source_data={},
            mapping_rules=[],
            array_mappings=[
                ArrayMappingConfig(
                    source_array_path="missing_array",
                    target_field="out",
                    item_mappings=[],
                ),
            ],
            strict_mode=False,
        )
        assert "out" not in result["mapped_data"]
        assert "missing_array" in result["skipped_rules"]

    def test_missing_array_strict(self, engine):
        result = engine.map_data(
            source_data={},
            mapping_rules=[],
            array_mappings=[
                ArrayMappingConfig(
                    source_array_path="missing_array",
                    target_field="out",
                    item_mappings=[],
                ),
            ],
            strict_mode=True,
        )
        assert len(result["errors"]) == 1


# -- Strict Mode ---------------------------------------------------------------


class TestStrictMode:
    def test_strict_fails_on_missing(self, engine):
        result = engine.map_data(
            source_data={"a": 1},
            mapping_rules=[
                MappingRule(source_path="a", target_field="exists", field_type="number"),
                MappingRule(source_path="b", target_field="missing_field"),
            ],
            array_mappings=[],
            strict_mode=True,
        )
        assert result["mapped_data"] == {"exists": 1}
        assert result["applied_rules_count"] == 1
        assert len(result["errors"]) == 1

    def test_lenient_skips_missing(self, engine):
        result = engine.map_data(
            source_data={"a": 1},
            mapping_rules=[
                MappingRule(source_path="a", target_field="exists", field_type="number"),
                MappingRule(source_path="b", target_field="missing_field"),
            ],
            array_mappings=[],
            strict_mode=False,
        )
        assert result["mapped_data"] == {"exists": 1}
        assert result["applied_rules_count"] == 1
        assert result["skipped_rules"] == ["b"]
        assert result["errors"] == []


# -- Edge Cases ----------------------------------------------------------------


class TestEdgeCases:
    def test_empty_rules(self, engine):
        result = engine.map_data(
            source_data={"a": 1},
            mapping_rules=[],
            array_mappings=[],
        )
        assert result["mapped_data"] == {}
        assert result["applied_rules_count"] == 0

    def test_empty_source_data(self, engine):
        result = engine.map_data(
            source_data={},
            mapping_rules=[MappingRule(source_path="a", target_field="b")],
            array_mappings=[],
            strict_mode=False,
        )
        assert result["mapped_data"] == {}
        assert result["skipped_rules"] == ["a"]

    def test_combined_direct_and_array(self, engine):
        result = engine.map_data(
            source_data={
                "status": "ok",
                "items": [{"x": 1}, {"x": 2}],
            },
            mapping_rules=[
                MappingRule(source_path="status", target_field="result_status"),
            ],
            array_mappings=[
                ArrayMappingConfig(
                    source_array_path="items",
                    target_field="values",
                    item_mappings=[MappingRule(source_path="x", target_field="val", field_type="number")],
                ),
            ],
        )
        assert result["mapped_data"]["result_status"] == "ok"
        assert result["mapped_data"]["values"] == [{"val": 1}, {"val": 2}]
        assert result["applied_rules_count"] == 2

    def test_out_of_bounds_array_index(self, engine):
        result = engine.map_data(
            source_data={"items": [1, 2]},
            mapping_rules=[MappingRule(source_path="items[5]", target_field="out")],
            array_mappings=[],
            strict_mode=False,
        )
        assert "out" not in result["mapped_data"]
        assert result["skipped_rules"] == ["items[5]"]


# -- Pluck / Values Mode ------------------------------------------------------


class TestPluckValuesMode:
    """Tests for output_mode='values' (extracting a flat array from array of objects)."""

    def test_pluck_single_field(self, engine):
        """[{a:1,b:2},{a:3,b:4}] → pluck 'a' → [1, 3]"""
        result = engine.map_data(
            source_data={"items": [{"a": 1, "b": 2, "c": 3}, {"a": 2, "b": 3, "c": 6}]},
            mapping_rules=[],
            array_mappings=[
                ArrayMappingConfig(
                    source_array_path="items",
                    target_field="a_values",
                    output_mode="values",
                    pluck_field="a",
                ),
            ],
        )
        assert result["mapped_data"]["a_values"] == [1, 2]
        assert result["applied_rules_count"] == 1
        assert result["errors"] == []

    def test_pluck_nested_field(self, engine):
        """Pluck nested path from array items."""
        result = engine.map_data(
            source_data={
                "users": [
                    {"name": {"first": "Alice"}},
                    {"name": {"first": "Bob"}},
                ],
            },
            mapping_rules=[],
            array_mappings=[
                ArrayMappingConfig(
                    source_array_path="users",
                    target_field="first_names",
                    output_mode="values",
                    pluck_field="name.first",
                ),
            ],
        )
        assert result["mapped_data"]["first_names"] == ["Alice", "Bob"]

    def test_pluck_missing_field_lenient(self, engine):
        """SA-1814 (G4): a missing pluck field emits null at that index — the
        output list stays index-aligned with the source (was: silently dropped)."""
        result = engine.map_data(
            source_data={"items": [{"a": 1}, {"b": 2}, {"a": 3}]},
            mapping_rules=[],
            array_mappings=[
                ArrayMappingConfig(
                    source_array_path="items",
                    target_field="a_values",
                    output_mode="values",
                    pluck_field="a",
                ),
            ],
            strict_mode=False,
        )
        assert result["mapped_data"]["a_values"] == [1, None, 3]

    def test_pluck_missing_field_strict(self, engine):
        """In strict mode, missing pluck fields produce errors — AND (SA-1814 G4)
        still emit null at that index so the list stays index-aligned."""
        result = engine.map_data(
            source_data={"items": [{"a": 1}, {"b": 2}]},
            mapping_rules=[],
            array_mappings=[
                ArrayMappingConfig(
                    source_array_path="items",
                    target_field="a_values",
                    output_mode="values",
                    pluck_field="a",
                ),
            ],
            strict_mode=True,
        )
        assert result["mapped_data"]["a_values"] == [1, None]
        assert len(result["errors"]) == 1
        assert "pluck field" in result["errors"][0].lower()

    def test_pluck_no_pluck_field_errors(self, engine):
        """output_mode='values' without pluck_field produces an error."""
        result = engine.map_data(
            source_data={"items": [{"a": 1}]},
            mapping_rules=[],
            array_mappings=[
                ArrayMappingConfig(
                    source_array_path="items",
                    target_field="out",
                    output_mode="values",
                ),
            ],
        )
        assert "out" not in result["mapped_data"]
        assert len(result["errors"]) == 1
        assert "pluck_field" in result["errors"][0]

    def test_pluck_from_flat_array(self, engine):
        """Pluck _value from a flat array like [1, 2, 3]."""
        result = engine.map_data(
            source_data={"nums": [10, 20, 30]},
            mapping_rules=[],
            array_mappings=[
                ArrayMappingConfig(
                    source_array_path="nums",
                    target_field="values",
                    output_mode="values",
                    pluck_field="_value",
                ),
            ],
        )
        assert result["mapped_data"]["values"] == [10, 20, 30]

    def test_pluck_with_string_values(self, engine):
        """Pluck string fields."""
        result = engine.map_data(
            source_data={
                "people": [
                    {"name": "Alice", "age": 30},
                    {"name": "Bob", "age": 25},
                    {"name": "Charlie", "age": 35},
                ],
            },
            mapping_rules=[],
            array_mappings=[
                ArrayMappingConfig(
                    source_array_path="people",
                    target_field="names",
                    output_mode="values",
                    pluck_field="name",
                ),
            ],
        )
        assert result["mapped_data"]["names"] == ["Alice", "Bob", "Charlie"]

    def test_objects_mode_reverse_flat_to_objects(self, engine):
        """[1, 2] → [{val:1}, {val:2}] using objects mode with _value trick."""
        result = engine.map_data(
            source_data={"nums": [1, 2]},
            mapping_rules=[],
            array_mappings=[
                ArrayMappingConfig(
                    source_array_path="nums",
                    target_field="wrapped",
                    output_mode="objects",
                    item_mappings=[
                        MappingRule(source_path="_value", target_field="val", field_type="number"),
                    ],
                ),
            ],
        )
        assert result["mapped_data"]["wrapped"] == [{"val": 1}, {"val": 2}]
