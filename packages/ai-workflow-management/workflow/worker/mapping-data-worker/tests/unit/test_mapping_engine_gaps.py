"""Gap-fill unit specs for DefaultMappingEngine — covers the coercion error
branches, nested-path edge returns, array-item strict/error handling, and the
unknown-field-type passthrough that the baseline suite left uncovered
(engine lines 114-119, 124-125, 135-136, 161, 175, 182, 188, 191-192, 198,
232, 253, 259, 266-267, 271, 277-280, 284, 290-293). Pure engine, no mocks;
all assertions grounded in default_mapping_engine.py source.
"""
import pytest

from app.layer1_domain.entities.mapping_rule import ArrayMappingConfig, MappingRule
from app.layer1_domain.value_objects.mapping_field_type import MappingFieldType
from app.layer4_frameworks.exceptions import TypeCoercionError
from app.layer4_frameworks.providers.default_mapping_engine import (
    DefaultMappingEngine,
    MISSING,
    _Missing,
)


@pytest.fixture
def engine():
    return DefaultMappingEngine()


# -- Unknown field_type passthrough (engine line 232) -------------------------


class TestUnknownFieldTypePassthrough:
    def test_unknown_field_type_returns_value_unchanged(self, engine):
        # edge: _coerce_value falls through every known type and returns the raw
        # value (line 232). A MappingRule can carry an arbitrary field_type string
        # (the dataclass does not validate it), so route directly through the engine.
        result = engine.map_data(
            source_data={"v": {"nested": True}},
            mapping_rules=[MappingRule(source_path="v", target_field="out", field_type="weird")],
            array_mappings=[],
        )
        # value passes through untouched (still a dict, not stringified)
        assert result["mapped_data"]["out"] == {"nested": True}
        assert result["applied_rules_count"] == 1


# -- Coercion error branches --------------------------------------------------


class TestNumberCoercionErrors:
    def test_bare_string_to_number_raises_recorded(self, engine):
        # failure path: non-numeric string → TypeCoercionError (line 252), caught
        # by map_data and recorded in errors.
        result = engine.map_data(
            source_data={"v": "abc"},
            mapping_rules=[MappingRule(source_path="v", target_field="n", field_type="number")],
            array_mappings=[],
        )
        assert result["mapped_data"] == {}
        assert len(result["errors"]) == 1
        assert "number" in result["errors"][0].lower()

    def test_non_string_non_number_to_number_raises(self, engine):
        # failure path: a dict cannot coerce to number (line 253).
        result = engine.map_data(
            source_data={"v": {"a": 1}},
            mapping_rules=[MappingRule(source_path="v", target_field="n", field_type="number")],
            array_mappings=[],
        )
        assert result["mapped_data"] == {}
        assert len(result["errors"]) == 1

    def test_list_to_number_raises(self, engine):
        # boundary: a list is neither int/float nor str → coercion error (line 253).
        result = engine.map_data(
            source_data={"v": [1, 2]},
            mapping_rules=[MappingRule(source_path="v", target_field="n", field_type="number")],
            array_mappings=[],
        )
        assert len(result["errors"]) == 1


class TestBooleanCoercionErrors:
    def test_unparseable_string_to_boolean_raises(self, engine):
        # failure path: string not in the true/false vocab → error (line 266).
        result = engine.map_data(
            source_data={"v": "maybe"},
            mapping_rules=[MappingRule(source_path="v", target_field="b", field_type="boolean")],
            array_mappings=[],
        )
        assert result["mapped_data"] == {}
        assert len(result["errors"]) == 1
        assert "boolean" in result["errors"][0].lower()

    def test_number_to_boolean(self, engine):
        # edge: numeric value coerces via bool() (line 259) — 0 → False, non-0 → True.
        result = engine.map_data(
            source_data={"z": 0, "n": 7},
            mapping_rules=[
                MappingRule(source_path="z", target_field="zf", field_type="boolean"),
                MappingRule(source_path="n", target_field="nf", field_type="boolean"),
            ],
            array_mappings=[],
        )
        assert result["mapped_data"] == {"zf": False, "nf": True}

    def test_dict_to_boolean_raises(self, engine):
        # boundary: a dict is not bool/number/str → coercion error (line 267).
        result = engine.map_data(
            source_data={"v": {"a": 1}},
            mapping_rules=[MappingRule(source_path="v", target_field="b", field_type="boolean")],
            array_mappings=[],
        )
        assert len(result["errors"]) == 1


class TestObjectCoercionErrors:
    def test_json_string_non_object_raises(self, engine):
        # failure path: valid JSON but not an object (line 277) → error.
        result = engine.map_data(
            source_data={"v": "[1, 2, 3]"},
            mapping_rules=[MappingRule(source_path="v", target_field="o", field_type="object")],
            array_mappings=[],
        )
        assert result["mapped_data"] == {}
        assert len(result["errors"]) == 1

    def test_invalid_json_to_object_raises(self, engine):
        # failure path: malformed JSON string (line 278-280) → error.
        result = engine.map_data(
            source_data={"v": "{not json"},
            mapping_rules=[MappingRule(source_path="v", target_field="o", field_type="object")],
            array_mappings=[],
        )
        assert len(result["errors"]) == 1

    def test_number_to_object_raises(self, engine):
        # boundary: an int is neither dict nor str → coercion error (line 280/284).
        result = engine.map_data(
            source_data={"v": 5},
            mapping_rules=[MappingRule(source_path="v", target_field="o", field_type="object")],
            array_mappings=[],
        )
        assert len(result["errors"]) == 1

    def test_dict_passthrough_to_object(self, engine):
        # happy: a dict returns as-is (line 271).
        result = engine.map_data(
            source_data={"v": {"a": 1}},
            mapping_rules=[MappingRule(source_path="v", target_field="o", field_type="object")],
            array_mappings=[],
        )
        assert result["mapped_data"]["o"] == {"a": 1}


class TestArrayCoercionErrors:
    def test_list_passthrough_to_array(self, engine):
        # happy: a list returns as-is (line 284).
        result = engine.map_data(
            source_data={"v": [1, 2]},
            mapping_rules=[MappingRule(source_path="v", target_field="a", field_type="array")],
            array_mappings=[],
        )
        assert result["mapped_data"]["a"] == [1, 2]

    def test_json_string_non_array_raises(self, engine):
        # failure path: valid JSON but object, not array (line 290) → error.
        result = engine.map_data(
            source_data={"v": '{"a": 1}'},
            mapping_rules=[MappingRule(source_path="v", target_field="a", field_type="array")],
            array_mappings=[],
        )
        assert len(result["errors"]) == 1

    def test_invalid_json_to_array_raises(self, engine):
        # failure path: malformed JSON string (line 291-293) → error.
        result = engine.map_data(
            source_data={"v": "[not json"},
            mapping_rules=[MappingRule(source_path="v", target_field="a", field_type="array")],
            array_mappings=[],
        )
        assert len(result["errors"]) == 1

    def test_number_to_array_raises(self, engine):
        # boundary: an int is neither list nor str → coercion error (line 293).
        result = engine.map_data(
            source_data={"v": 9},
            mapping_rules=[MappingRule(source_path="v", target_field="a", field_type="array")],
            array_mappings=[],
        )
        assert len(result["errors"]) == 1


# -- Nested-path edge returns (engine lines 161, 175, 182, 188, 191-192, 198) --


class TestNestedPathEdges:
    def test_empty_path_returns_missing(self, engine):
        # edge: an empty source_path resolves to MISSING (line 175); in DIRECT mode
        # with no default the rule is skipped in lenient mode.
        result = engine.map_data(
            source_data={"a": 1},
            mapping_rules=[MappingRule(source_path="", target_field="out")],
            array_mappings=[],
            strict_mode=False,
        )
        assert "out" not in result["mapped_data"]

    def test_index_into_non_list_returns_missing(self, engine):
        # invalid input: bracket index against a dict (not a list) → MISSING (line 188).
        result = engine.map_data(
            source_data={"a": {"b": 1}},
            mapping_rules=[MappingRule(source_path="a[0]", target_field="out")],
            array_mappings=[],
            strict_mode=False,
        )
        assert "out" not in result["mapped_data"]
        assert "a[0]" in result["skipped_rules"]

    def test_non_integer_index_returns_missing(self, engine):
        # invalid input: non-numeric bracket index → MISSING (line 191-192).
        result = engine.map_data(
            source_data={"a": [1, 2, 3]},
            mapping_rules=[MappingRule(source_path="a[x]", target_field="out")],
            array_mappings=[],
            strict_mode=False,
        )
        assert "out" not in result["mapped_data"]

    def test_traverse_key_on_non_dict_returns_missing(self, engine):
        # invalid input: dotted key into a scalar (not a dict) → MISSING (line 198).
        result = engine.map_data(
            source_data={"a": 5},
            mapping_rules=[MappingRule(source_path="a.b", target_field="out")],
            array_mappings=[],
            strict_mode=False,
        )
        assert "out" not in result["mapped_data"]

    def test_direct_null_value_no_default_treated_as_missing(self, engine):
        # SA-1813: a null source with no default is now treated as MISSING and
        # follows on_missing (default lenient -> skip), instead of coercing to the
        # literal string "None" (the old footgun).
        result = engine.map_data(
            source_data={"a": None},
            mapping_rules=[MappingRule(source_path="a", target_field="out")],
            array_mappings=[],
        )
        assert "out" not in result["mapped_data"]
        assert "a" in result["skipped_rules"]


# -- Array item strict/error handling (engine lines 114-119, 124-125) ---------


class TestArrayItemHandling:
    def test_array_item_missing_field_strict_records_error(self, engine):
        # failure path: in strict mode, an item missing a sub-rule field records a
        # per-item error (lines 113-118) rather than silently skipping.
        result = engine.map_data(
            source_data={"items": [{"name": "A"}, {"other": "B"}]},
            mapping_rules=[],
            array_mappings=[
                ArrayMappingConfig(
                    source_array_path="items",
                    target_field="out",
                    item_mappings=[MappingRule(source_path="name", target_field="n")],
                ),
            ],
            strict_mode=True,
        )
        # second item lacks 'name' → one strict error; first item still mapped
        assert result["mapped_data"]["out"][0] == {"n": "A"}
        assert result["mapped_data"]["out"][1] == {}
        assert len(result["errors"]) == 1
        assert "items[1]" in result["errors"][0]

    def test_array_item_coercion_error_recorded_per_item(self, engine):
        # failure path: a sub-rule coercion failure inside an item is caught and
        # recorded (lines 124-125), leaving the rest of the item intact.
        result = engine.map_data(
            source_data={"items": [{"n": "notnum", "label": "keep"}]},
            mapping_rules=[],
            array_mappings=[
                ArrayMappingConfig(
                    source_array_path="items",
                    target_field="out",
                    item_mappings=[
                        MappingRule(source_path="label", target_field="label"),
                        MappingRule(source_path="n", target_field="num", field_type="number"),
                    ],
                ),
            ],
        )
        assert result["mapped_data"]["out"][0] == {"label": "keep"}
        assert len(result["errors"]) == 1
        assert "items[0]" in result["errors"][0]


# -- MISSING sentinel identity ------------------------------------------------


class TestMissingSentinel:
    def test_missing_is_missing_instance(self):
        assert isinstance(MISSING, _Missing)


# -- Generic exception fallbacks + defensive guards ---------------------------


class _ExplodingRule:
    """A rule-shaped object whose field_type access raises a non-coercion error.

    Exercises the generic ``except Exception`` fallback that guards the rule loop
    (engine lines 52-53) — a robustness branch not reachable with a well-formed
    MappingRule, so we feed a deliberately malformed rule object.
    """
    mapping_mode = "direct"
    source_path = "a"
    target_field = "out"
    default_value = None

    @property
    def field_type(self):
        raise RuntimeError("boom")


class TestGenericExceptionFallbacks:
    def test_direct_rule_generic_exception_recorded(self, engine):
        # failure path: an unexpected (non-coercion) error inside the rule loop is
        # caught by the generic handler (lines 52-53) and recorded, not raised.
        result = engine.map_data(
            source_data={"a": 1},
            mapping_rules=[_ExplodingRule()],
            array_mappings=[],
        )
        assert result["mapped_data"] == {}
        assert len(result["errors"]) == 1
        assert "out" in result["errors"][0]

    def test_array_mapping_generic_exception_recorded(self, engine):
        # failure path: an unexpected error while processing an array mapping is
        # caught by the outer generic handler (engine lines 135-136). We force it
        # by giving output_mode="values" a pluck_field whose access explodes via a
        # config-shaped object.
        class _ExplodingArrayCfg:
            source_array_path = "items"
            target_field = "out"
            output_mode = "values"
            item_mappings = []

            @property
            def pluck_field(self):
                raise RuntimeError("array boom")

        result = engine.map_data(
            source_data={"items": [{"a": 1}]},
            mapping_rules=[],
            array_mappings=[_ExplodingArrayCfg()],
        )
        assert "out" not in result["mapped_data"]
        assert len(result["errors"]) == 1
        assert "items" in result["errors"][0]


class TestNestedValueDefensiveGuard:
    def test_get_nested_value_short_circuits_on_missing_data(self, engine):
        # defensive guard (engine line 182): if traversal is entered with the
        # MISSING sentinel as the current value, it short-circuits to MISSING
        # rather than attempting attribute/key access. Reached by calling the
        # engine's own resolver directly (still testing the unit).
        assert engine._get_nested_value(MISSING, "a.b") is MISSING
