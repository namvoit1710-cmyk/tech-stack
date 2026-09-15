"""Unit tests for variable_resolver.py — expression parsing, resolution, conditions."""

from __future__ import annotations

import pytest

from app.layer2_application.resolve.variable_resolver import (
    ParsedExpression,
    parse_expression,
    find_all_expressions,
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
    VariableResolutionError,
    MappingResolutionError,
    ConditionEvaluator,
    _coerce,
    _numeric,
    UNRESOLVED,
)


# ────────────── ParsedExpression ──────────────


class TestParsedExpression:
    def test_is_empty_path_true(self):
        pe = ParsedExpression(raw="{{$input}}", source="input", path_parts=())
        assert pe.is_empty_path is True

    def test_is_empty_path_false(self):
        pe = ParsedExpression(raw="{{$input.data}}", source="input", path_parts=("data",))
        assert pe.is_empty_path is False


# ────────────── parse_expression ──────────────


class TestParseExpression:
    def test_simple_source_no_path(self):
        result = parse_expression("{{$input}}")
        assert result is not None
        assert result.source == "input"
        assert result.path_parts == ()

    def test_source_with_dotted_path(self):
        result = parse_expression("{{$node1.data.name}}")
        assert result is not None
        assert result.source == "node1"
        assert result.path_parts == ("data", "name")

    def test_source_with_index(self):
        result = parse_expression("{{$node1.data[0].name}}")
        assert result is not None
        assert result.source == "node1"
        assert result.path_parts == ("data", 0, "name")

    def test_multiple_indices(self):
        result = parse_expression("{{$src[0][1]}}")
        assert result is not None
        assert result.path_parts == (0, 1)

    def test_invalid_expression_no_braces(self):
        assert parse_expression("hello") is None

    def test_invalid_expression_no_dollar(self):
        assert parse_expression("{{node1.data}}") is None

    def test_invalid_expression_partial_braces(self):
        assert parse_expression("{$input}") is None

    def test_whitespace_around_expression(self):
        result = parse_expression("  {{$input}}  ")
        assert result is not None
        assert result.source == "input"

    def test_hyphen_in_source(self):
        result = parse_expression("{{$my-node.data}}")
        assert result is not None
        assert result.source == "my-node"
        assert result.path_parts == ("data",)

    def test_empty_string(self):
        assert parse_expression("") is None


# ────────────── find_all_expressions ──────────────


class TestFindAllExpressions:
    def test_single_expression(self):
        results = find_all_expressions("Value is {{$node1.data}}")
        assert len(results) == 1
        assert results[0].source == "node1"

    def test_multiple_expressions(self):
        text = "Hello {{$input.name}}, you have {{$node1.count}} items"
        results = find_all_expressions(text)
        assert len(results) == 2
        assert results[0].source == "input"
        assert results[1].source == "node1"

    def test_no_expressions(self):
        results = find_all_expressions("plain text")
        assert results == []

    def test_empty_string(self):
        results = find_all_expressions("")
        assert results == []


# ────────────── MetadataResolverStrategy ──────────────


class TestMetadataResolverStrategy:
    def setup_method(self):
        self.strategy = MetadataResolverStrategy()

    async def test_happy_path(self):
        source_data = {
            "metadata": {
                "extracted": {
                    "$.name": {"value": "Alice", "requires_fetch": False},
                }
            }
        }
        result = await self.strategy.try_resolve(source_data, ("name",))
        assert result == "Alice"

    async def test_requires_fetch_returns_unresolved(self):
        source_data = {
            "metadata": {
                "extracted": {
                    "$.name": {"value": "Alice", "requires_fetch": True},
                }
            }
        }
        result = await self.strategy.try_resolve(source_data, ("name",))
        assert result is UNRESOLVED

    async def test_non_dict_source(self):
        result = await self.strategy.try_resolve("not a dict", ("name",))
        assert result is UNRESOLVED

    async def test_no_metadata_key(self):
        result = await self.strategy.try_resolve({"other": "data"}, ("name",))
        assert result is UNRESOLVED

    async def test_metadata_not_dict(self):
        result = await self.strategy.try_resolve({"metadata": "string"}, ("name",))
        assert result is UNRESOLVED

    async def test_no_extracted_key(self):
        result = await self.strategy.try_resolve({"metadata": {"other": 1}}, ("name",))
        assert result is UNRESOLVED

    async def test_extracted_not_dict(self):
        result = await self.strategy.try_resolve({"metadata": {"extracted": "str"}}, ("name",))
        assert result is UNRESOLVED

    async def test_missing_json_path_key(self):
        source_data = {
            "metadata": {
                "extracted": {
                    "$.other": {"value": "Bob"},
                }
            }
        }
        result = await self.strategy.try_resolve(source_data, ("name",))
        assert result is UNRESOLVED

    async def test_integer_path_parts(self):
        source_data = {
            "metadata": {
                "extracted": {
                    "$.[0].name": {"value": "first"},
                }
            }
        }
        result = await self.strategy.try_resolve(source_data, (0, "name"))
        assert result == "first"

    async def test_entry_without_value_key(self):
        source_data = {
            "metadata": {
                "extracted": {
                    "$.name": {"something": "else"},
                }
            }
        }
        result = await self.strategy.try_resolve(source_data, ("name",))
        assert result is UNRESOLVED


# ────────────── TraversalResolverStrategy ──────────────


class TestTraversalResolverStrategy:
    def setup_method(self):
        self.strategy = TraversalResolverStrategy()

    async def test_dict_traversal(self):
        data = {"output": {"name": "Alice"}}
        result = await self.strategy.try_resolve(data, ("output", "name"))
        assert result == "Alice"

    async def test_list_index(self):
        data = {"items": ["a", "b", "c"]}
        result = await self.strategy.try_resolve(data, ("items", 1))
        assert result == "b"

    async def test_nested_dict_and_list(self):
        data = {"rows": [{"id": 1}, {"id": 2}]}
        result = await self.strategy.try_resolve(data, ("rows", 0, "id"))
        assert result == 1

    async def test_key_not_found(self):
        with pytest.raises(VariableResolutionError, match="Key 'missing' not found"):
            await self.strategy.try_resolve({"a": 1}, ("missing",))

    async def test_index_out_of_range(self):
        with pytest.raises(VariableResolutionError, match="Index 5 out of range"):
            await self.strategy.try_resolve([1, 2], (5,))

    async def test_non_traversable_type_for_int_index(self):
        with pytest.raises(VariableResolutionError, match="Expected list"):
            await self.strategy.try_resolve({"a": "string"}, ("a", 0))

    async def test_non_traversable_type_for_str_key(self):
        with pytest.raises(VariableResolutionError, match="Cannot traverse"):
            await self.strategy.try_resolve(42, ("key",))

    async def test_empty_path(self):
        data = {"x": 1}
        result = await self.strategy.try_resolve(data, ())
        assert result == {"x": 1}

    async def test_tuple_index(self):
        data = (10, 20, 30)
        result = await self.strategy.try_resolve(data, (2,))
        assert result == 30


# ────────────── VariableResolver ──────────────


class TestVariableResolver:
    def setup_method(self):
        self.resolver = VariableResolver([
            MetadataResolverStrategy(),
            TraversalResolverStrategy(),
        ])

    async def test_resolve_single_expression(self):
        context = {"node1": {"output": {"name": "Alice"}}}
        result = await self.resolver.resolve("{{$node1.output.name}}", context)
        assert result == "Alice"

    async def test_resolve_inline_expressions(self):
        context = {"input": {"name": "Alice", "age": 30}}
        result = await self.resolver.resolve(
            "Hello {{$input.name}}, age {{$input.age}}", context
        )
        assert result == "Hello Alice, age 30"

    async def test_resolve_no_expression(self):
        result = await self.resolver.resolve("plain text", {})
        assert result == "plain text"

    async def test_resolve_non_string(self):
        result = await self.resolver.resolve(42, {})
        assert result == 42

    async def test_resolve_source_not_found(self):
        with pytest.raises(VariableResolutionError, match="Source 'missing' not found"):
            await self.resolver.resolve("{{$missing.data}}", {})

    async def test_resolve_empty_path(self):
        context = {"input": {"a": 1}}
        result = await self.resolver.resolve("{{$input}}", context)
        assert result == {"a": 1}

    async def test_resolve_mapping(self):
        context = {"node1": {"val": 42}}
        mapping = {"key1": "{{$node1.val}}", "key2": "static"}
        result = await self.resolver.resolve_mapping(mapping, context)
        assert result == {"key1": 42, "key2": "static"}

    async def test_resolve_value_nested_dict(self):
        context = {"n": {"x": 1}}
        mapping = {"outer": {"inner": "{{$n.x}}"}}
        result = await self.resolver.resolve_mapping(mapping, context)
        assert result == {"outer": {"inner": 1}}

    async def test_resolve_value_nested_list(self):
        context = {"n": {"x": 1}}
        mapping = {"items": ["{{$n.x}}", "static"]}
        result = await self.resolver.resolve_mapping(mapping, context)
        assert result == {"items": [1, "static"]}

    async def test_resolve_value_non_string_passthrough(self):
        context = {}
        mapping = {"num": 42, "flag": True, "nothing": None}
        result = await self.resolver.resolve_mapping(mapping, context)
        assert result == {"num": 42, "flag": True, "nothing": None}

    async def test_no_strategy_resolves(self):
        resolver = VariableResolver([])
        context = {"node1": {"data": "value"}}
        with pytest.raises(VariableResolutionError, match="No strategy could resolve"):
            await resolver.resolve("{{$node1.data}}", context)

    async def test_resolve_inline_dict_uses_json_format(self):
        """When a dict is interpolated into a string, it must use JSON format."""
        context = {"node1": {"data": {"isActive": True, "count": 5}}}
        result = await self.resolver.resolve("body: {{$node1.data}}", context)
        assert "true" in result
        assert "True" not in result
        assert '"isActive"' in result

    async def test_resolve_inline_list_uses_json_format(self):
        """When a list is interpolated into a string, it must use JSON format."""
        context = {"node1": {"items": [True, False, None]}}
        result = await self.resolver.resolve("items: {{$node1.items}}", context)
        assert "true" in result
        assert "false" in result
        assert "null" in result
        assert "True" not in result
        assert "False" not in result
        assert "None" not in result

    async def test_resolve_inline_scalar_unchanged(self):
        """Scalar values should still use str() for interpolation."""
        context = {"input": {"name": "Alice", "age": 30}}
        result = await self.resolver.resolve(
            "Name: {{$input.name}}, Age: {{$input.age}}", context
        )
        assert result == "Name: Alice, Age: 30"

    async def test_metadata_strategy_priority(self):
        """Metadata strategy should be tried first and return if successful."""
        context = {
            "node1": {
                "output": {"name": "traversal"},
                "metadata": {
                    "extracted": {
                        "$.name": {"value": "metadata"},
                    }
                },
            }
        }
        resolver = VariableResolver([
            MetadataResolverStrategy(),
            TraversalResolverStrategy(),
        ])
        result = await resolver.resolve("{{$node1.name}}", context)
        assert result == "metadata"


# ────────────── Legacy path rewrite (backward compat) ──────────────


class TestLegacyPathRewrite:
    """Backward-compat: INPUT/HUMAN_ACTION used to wrap data as
    {"output": ..., "metadata": {"action": ...}}. Wrapping was removed;
    legacy expressions are rewritten at resolution time as a fallback."""

    def setup_method(self):
        self.resolver = VariableResolver([TraversalResolverStrategy()])

    async def test_output_prefix_stripped_when_original_fails(self):
        """`output.name` falls through to `name` on unwrapped source."""
        context = {"input": {"name": "Alice", "age": 30}}
        result = await self.resolver.resolve("{{$input.output.name}}", context)
        assert result == "Alice"

    async def test_output_prefix_with_nested_path(self):
        """`output.user.id` falls through to `user.id`."""
        context = {"input": {"user": {"id": "u-42"}}}
        result = await self.resolver.resolve("{{$input.output.user.id}}", context)
        assert result == "u-42"

    async def test_metadata_action_rewrites_to_underscore_action(self):
        """`metadata.action` falls through to `_action`."""
        context = {"ha": {"_action": "confirm", "comments": "ok"}}
        result = await self.resolver.resolve("{{$ha.metadata.action}}", context)
        assert result == "confirm"

    async def test_legitimate_output_field_not_rewritten(self):
        """If `output` exists as a real top-level field, original wins."""
        context = {"input": {"output": "hello world"}}
        result = await self.resolver.resolve("{{$input.output}}", context)
        assert result == "hello world"

    async def test_rewrite_only_on_original_failure(self):
        """If original path resolves, no rewrite is attempted."""
        # `output` key exists but contains a dict → original path wins
        context = {"input": {"output": {"field": "original"}, "field": "rewritten"}}
        result = await self.resolver.resolve("{{$input.output.field}}", context)
        assert result == "original"

    async def test_metadata_type_not_supported(self):
        """metadata.type is documented as unsupported (no rewrite)."""
        context = {"ha": {"_action": "confirm"}}
        with pytest.raises(VariableResolutionError):
            await self.resolver.resolve("{{$ha.metadata.type}}", context)

    async def test_output_prefix_on_missing_field_still_errors(self):
        """If rewrite doesn't resolve either, proper error is raised."""
        context = {"input": {"name": "Alice"}}
        with pytest.raises(VariableResolutionError):
            await self.resolver.resolve("{{$input.output.missing}}", context)


# ────────────── ConditionEvaluator ──────────────


class TestConditionEvaluator:
    def setup_method(self):
        resolver = VariableResolver([TraversalResolverStrategy()])
        self.evaluator = ConditionEvaluator(resolver)

    async def test_empty_condition(self):
        assert await self.evaluator.evaluate("", {}) is True

    async def test_whitespace_condition(self):
        assert await self.evaluator.evaluate("   ", {}) is True

    async def test_none_condition(self):
        assert await self.evaluator.evaluate(None, {}) is True

    async def test_and_condition(self):
        ctx = {"a": "true", "b": "true"}
        assert await self.evaluator.evaluate("{{$a}} AND {{$b}}", ctx) is True

    async def test_and_condition_false(self):
        ctx = {"a": "true", "b": "false"}
        assert await self.evaluator.evaluate("{{$a}} AND {{$b}}", ctx) is False

    async def test_or_condition(self):
        ctx = {"a": "false", "b": "true"}
        assert await self.evaluator.evaluate("{{$a}} OR {{$b}}", ctx) is True

    async def test_or_condition_all_false(self):
        ctx = {"a": "false", "b": "false"}
        assert await self.evaluator.evaluate("{{$a}} OR {{$b}}", ctx) is False

    # ── SA-1905 D2: file_ref truthiness (whole-value consumer) ──
    # A condition on a whole file-backed value ({{$node.items}}) resolves to
    # a file_ref dict. bool(non-empty-dict) was always True, so an EMPTY
    # collection wrongly took the true branch. Truthiness must mean "has
    # data" (row_count / artifacts / columns) — cheaply, no download.

    async def test_condition_file_ref_nonempty_is_true(self):
        ref = {"__file_ref": True, "file_id": "f1", "content_type": "text/csv",
               "row_count": 3, "columns": [], "column_types": {}, "artifacts": []}
        ctx = {"node": {"items": ref}}
        assert await self.evaluator.evaluate("{{$node.items}}", ctx) is True

    async def test_condition_file_ref_empty_is_false(self):
        ref = {"__file_ref": True, "file_id": "", "content_type": "text/csv",
               "row_count": 0, "columns": [], "column_types": {}, "artifacts": []}
        ctx = {"node": {"items": ref}}
        assert await self.evaluator.evaluate("{{$node.items}}", ctx) is False

    async def test_condition_file_ref_with_columns_is_true(self):
        # A single-row object stored as a ref (row_count 1, real columns) exists → truthy.
        ref = {"__file_ref": True, "file_id": "f2", "content_type": "text/csv",
               "row_count": 1, "columns": ["name"], "column_types": {}, "artifacts": []}
        ctx = {"node": {"obj": ref}}
        assert await self.evaluator.evaluate("{{$node.obj}}", ctx) is True

    async def test_operator_eq(self):
        ctx = {"a": "5 == 5"}
        assert await self.evaluator.evaluate("{{$a}}", ctx) is True

    async def test_operator_neq(self):
        ctx = {"a": "5 != 3"}
        assert await self.evaluator.evaluate("{{$a}}", ctx) is True

    async def test_operator_gt(self):
        ctx = {"a": "10 > 5"}
        assert await self.evaluator.evaluate("{{$a}}", ctx) is True

    async def test_operator_gte(self):
        ctx = {"a": "10 >= 10"}
        assert await self.evaluator.evaluate("{{$a}}", ctx) is True

    async def test_operator_lt(self):
        ctx = {"a": "3 < 5"}
        assert await self.evaluator.evaluate("{{$a}}", ctx) is True

    async def test_operator_lte(self):
        ctx = {"a": "5 <= 5"}
        assert await self.evaluator.evaluate("{{$a}}", ctx) is True

    async def test_operator_in(self):
        ctx = {"a": "'abc' IN 'xabcx'"}
        assert await self.evaluator.evaluate("{{$a}}", ctx) is True

    async def test_operator_contains(self):
        ctx = {"a": "'hello world' CONTAINS 'world'"}
        assert await self.evaluator.evaluate("{{$a}}", ctx) is True

    async def test_bool_resolved_true(self):
        ctx = {"a": True}
        assert await self.evaluator.evaluate("{{$a}}", ctx) is True

    async def test_bool_resolved_false(self):
        ctx = {"a": False}
        assert await self.evaluator.evaluate("{{$a}}", ctx) is False

    async def test_non_string_non_bool_resolved(self):
        ctx = {"a": 42}
        assert await self.evaluator.evaluate("{{$a}}", ctx) is True

    async def test_non_string_non_bool_zero(self):
        ctx = {"a": 0}
        assert await self.evaluator.evaluate("{{$a}}", ctx) is False

    async def test_coerce_fallback(self):
        ctx = {"a": "true"}
        assert await self.evaluator.evaluate("{{$a}}", ctx) is True

    async def test_eq_false(self):
        ctx = {"a": "5 == 6"}
        assert await self.evaluator.evaluate("{{$a}}", ctx) is False

    async def test_gt_false(self):
        ctx = {"a": "3 > 5"}
        assert await self.evaluator.evaluate("{{$a}}", ctx) is False

    async def test_operator_in_list_like(self):
        ctx = {"a": "'x' IN 'xyz'"}
        assert await self.evaluator.evaluate("{{$a}}", ctx) is True

    async def test_operator_contains_list_like(self):
        ctx = {"a": "'xyz' CONTAINS 'y'"}
        assert await self.evaluator.evaluate("{{$a}}", ctx) is True

    async def test_operator_in_with_non_string_types(self):
        """Covers the IN operator where right is a quoted string containing left."""
        ctx = {"a": "3 IN '12345'"}
        assert await self.evaluator.evaluate("{{$a}}", ctx) is True

    async def test_operator_contains_with_non_string_right_coerced(self):
        """Covers CONTAINS where left is a string containing str(right)."""
        ctx = {"a": "'hello world' CONTAINS 'world'"}
        assert await self.evaluator.evaluate("{{$a}}", ctx) is True


# ────────────── _coerce ──────────────


class TestCoerce:
    def test_quoted_string_double(self):
        assert _coerce('"hello"') == "hello"

    def test_quoted_string_single(self):
        assert _coerce("'hello'") == "hello"

    def test_true(self):
        assert _coerce("true") is True
        assert _coerce("True") is True
        assert _coerce("TRUE") is True

    def test_false(self):
        assert _coerce("false") is False
        assert _coerce("False") is False

    def test_null(self):
        assert _coerce("null") is None
        assert _coerce("none") is None

    def test_int(self):
        assert _coerce("42") == 42

    def test_float(self):
        assert _coerce("3.14") == 3.14

    def test_plain_string(self):
        assert _coerce("hello") == "hello"


# ────────────── _numeric ──────────────


class TestNumeric:
    def test_int_value(self):
        assert _numeric(10) == 10.0

    def test_float_value(self):
        assert _numeric(3.14) == 3.14

    def test_string_numeric(self):
        assert _numeric("7") == 7.0

    def test_non_numeric(self):
        with pytest.raises(VariableResolutionError, match="Cannot compare non-numeric"):
            _numeric("abc")


# ────────────── UNRESOLVED singleton ──────────────


class TestUnresolved:
    def test_singleton(self):
        from app.layer2_application.resolve.variable_resolver import _UnresolvedType
        a = _UnresolvedType()
        b = _UnresolvedType()
        assert a is b

    def test_repr(self):
        assert repr(UNRESOLVED) == "UNRESOLVED"

    def test_bool_false(self):
        assert bool(UNRESOLVED) is False


# ────────────── Better Error Messages (Improvement 2) ──────────────


class TestBetterErrorMessages:
    def setup_method(self):
        self.resolver = VariableResolver([
            MetadataResolverStrategy(),
            TraversalResolverStrategy(),
        ])

    async def test_source_not_found_shows_available(self):
        ctx = {"node1": {"x": 1}, "input": {"y": 2}}
        with pytest.raises(VariableResolutionError, match="Available sources:") as exc_info:
            await self.resolver.resolve("{{$missing.data}}", ctx)
        assert "input" in str(exc_info.value)
        assert "node1" in str(exc_info.value)

    async def test_key_not_found_shows_available(self):
        ctx = {"node1": {"alpha": 1, "beta": 2}}
        with pytest.raises(VariableResolutionError, match="Available keys:") as exc_info:
            await self.resolver.resolve("{{$node1.gamma}}", ctx)
        assert "alpha" in str(exc_info.value)
        assert "beta" in str(exc_info.value)

    async def test_no_strategy_shows_available_keys_at_root(self):
        resolver = VariableResolver([])
        ctx = {"node1": {"foo": 1, "bar": 2}}
        with pytest.raises(VariableResolutionError, match="Available keys at root:") as exc_info:
            await resolver.resolve("{{$node1.foo}}", ctx)
        assert "bar" in str(exc_info.value)
        assert "foo" in str(exc_info.value)


# ────────────── Type-Aware Resolution (Improvement 3) ──────────────


class TestCoerceValue:
    def test_string_to_number_int(self):
        assert VariableResolver.coerce_value("42", "number") == 42

    def test_string_to_number_float(self):
        assert VariableResolver.coerce_value("3.14", "number") == 3.14

    def test_int_passthrough(self):
        assert VariableResolver.coerce_value(42, "number") == 42

    def test_float_passthrough(self):
        assert VariableResolver.coerce_value(3.14, "number") == 3.14

    def test_string_to_boolean_true(self):
        assert VariableResolver.coerce_value("true", "boolean") is True

    def test_string_to_boolean_yes(self):
        assert VariableResolver.coerce_value("yes", "boolean") is True

    def test_string_to_boolean_false(self):
        assert VariableResolver.coerce_value("false", "boolean") is False

    def test_string_to_boolean_empty(self):
        assert VariableResolver.coerce_value("", "boolean") is False

    def test_int_to_boolean(self):
        assert VariableResolver.coerce_value(1, "boolean") is True
        assert VariableResolver.coerce_value(0, "boolean") is False

    def test_number_to_string(self):
        assert VariableResolver.coerce_value(42, "string") == "42"

    def test_dict_to_string_passes_through(self):
        # A structured value declared "string" must NOT become a Python-repr
        # string ("{'a': 1}"); surface the real dict instead.
        assert VariableResolver.coerce_value({"a": 1}, "string") == {"a": 1}

    def test_list_to_string_passes_through(self):
        assert VariableResolver.coerce_value([{"id": 1}], "string") == [{"id": 1}]

    def test_any_passthrough(self):
        assert VariableResolver.coerce_value("hello", "any") == "hello"

    def test_none_passthrough(self):
        assert VariableResolver.coerce_value(None, "number") is None

    def test_invalid_number_raises(self):
        with pytest.raises(VariableResolutionError, match="Cannot coerce"):
            VariableResolver.coerce_value("abc", "number")

    def test_invalid_boolean_raises(self):
        with pytest.raises(VariableResolutionError, match="Cannot coerce"):
            VariableResolver.coerce_value([1, 2], "boolean")

    def test_non_dict_to_object_raises(self):
        with pytest.raises(VariableResolutionError, match="Cannot coerce"):
            VariableResolver.coerce_value("string", "object")

    def test_dict_to_object(self):
        assert VariableResolver.coerce_value({"a": 1}, "object") == {"a": 1}

    def test_list_to_array(self):
        assert VariableResolver.coerce_value([1, 2], "array") == [1, 2]

    def test_non_list_to_array_raises(self):
        with pytest.raises(VariableResolutionError, match="Cannot coerce"):
            VariableResolver.coerce_value("string", "array")

    def test_unknown_type_passthrough(self):
        assert VariableResolver.coerce_value("hello", "custom") == "hello"

    def test_empty_target_type_passthrough(self):
        assert VariableResolver.coerce_value("hello", "") == "hello"

    def test_file_ref_to_array_passes_through(self):
        # SA-1905: a streamed output is a file_ref pointer standing in for a
        # (possibly huge) array. Coercing to array must pass the ref through,
        # NOT raise / materialize — that's how the OUTPUT node + any array-typed
        # binding accepts {{$gen.output.result}} without loading 120k rows.
        from app.layer1_domain.entities.data_reference import build_file_ref
        ref = build_file_ref(file_id="f-1", row_count=120000, columns=["i", "sku"])
        assert VariableResolver.coerce_value(ref, "array") is ref

    def test_file_ref_to_object_passes_through(self):
        from app.layer1_domain.entities.data_reference import build_file_ref
        ref = build_file_ref(file_id="f-1", columns=["i"])
        assert VariableResolver.coerce_value(ref, "object") is ref

    def test_plain_dict_to_array_still_raises(self):
        # A non-file_ref dict is still an honest type error — only file_refs
        # get the pass-through.
        with pytest.raises(VariableResolutionError, match="Cannot coerce"):
            VariableResolver.coerce_value({"a": 1}, "array")


class TestResolveMappingWithTypeMap:
    def setup_method(self):
        self.resolver = VariableResolver([TraversalResolverStrategy()])

    async def test_type_map_coerces_number(self):
        ctx = {"n": {"count": "5"}}
        result = await self.resolver.resolve_mapping(
            {"count": "{{$n.count}}"}, ctx, type_map={"count": "number"}
        )
        assert result == {"count": 5}

    async def test_type_map_none_no_coercion(self):
        ctx = {"n": {"count": "5"}}
        result = await self.resolver.resolve_mapping(
            {"count": "{{$n.count}}"}, ctx, type_map=None
        )
        assert result == {"count": "5"}

    async def test_type_map_partial(self):
        ctx = {"n": {"a": "42", "b": "hello"}}
        result = await self.resolver.resolve_mapping(
            {"a": "{{$n.a}}", "b": "{{$n.b}}"},
            ctx,
            type_map={"a": "number"},
        )
        assert result == {"a": 42, "b": "hello"}

    async def test_strict_coercion_raises_on_mismatch(self):
        ctx = {"n": {"arr": '[{"field": "country"}]'}}
        with pytest.raises(VariableResolutionError):
            await self.resolver.resolve_mapping(
                {"arr": "{{$n.arr}}"}, ctx, type_map={"arr": "array"},
            )

    async def test_lenient_coercion_keeps_uncoercible_value(self):
        # A field declared ``array`` carrying a JSON-encoded string (a code
        # worker json.loads it itself) must survive coercion as the raw string.
        json_str = '[{"field": "country", "value": "VN"}]'
        ctx = {"n": {"arr": json_str}}
        result = await self.resolver.resolve_mapping(
            {"arr": "{{$n.arr}}"}, ctx, type_map={"arr": "array"},
            coerce_strict=False,
        )
        assert result == {"arr": json_str}


# ────────────── D2/D3 — the failure report ──────────────


class TestTheReportNamesTheFieldAndTheExpression:
    """D2. The bug that motivated this: a Jira node failed with

        VALIDATION_ERROR: 'issue_key' is required

    while ``issue_key`` had resolved perfectly to 'SA-2282'. The real culprit
    was ``{{$rootcause.metadata.score}}``, embedded in a different field's
    body. Whoever reads the error must be told which field and which token,
    or they go and "fix" the expression that was never broken.
    """

    def setup_method(self):
        self.resolver = VariableResolver([TraversalResolverStrategy()])

    async def test_it_names_the_failing_key(self):
        ctx = {"get_issue": {"issue_key": "SA-2282"}, "rootcause": {}}
        with pytest.raises(MappingResolutionError) as caught:
            await self.resolver.resolve_mapping(
                {
                    "issue_key": "{{$get_issue.issue_key}}",
                    "body": "confidence {{$rootcause.metadata.score}}",
                },
                ctx,
            )
        assert [f.key for f in caught.value.failures] == ["body"]

    async def test_it_names_the_embedded_token_not_the_whole_template(self):
        """The field body is a 200-character template. Reporting all of it is
        the same as reporting none of it."""
        ctx = {"rootcause": {}}
        body = ("Root cause analysis complete. " * 6) + \
               "confidence {{$rootcause.metadata.score}} — see attached."
        with pytest.raises(MappingResolutionError) as caught:
            await self.resolver.resolve_mapping({"body": body}, ctx)

        (failure,) = caught.value.failures
        assert failure.expression == "{{$rootcause.metadata.score}}"

    async def test_the_key_that_worked_is_not_blamed(self):
        ctx = {"get_issue": {"issue_key": "SA-2282"}, "rootcause": {}}
        with pytest.raises(MappingResolutionError) as caught:
            await self.resolver.resolve_mapping(
                {"issue_key": "{{$get_issue.issue_key}}",
                 "body": "{{$rootcause.metadata.score}}"},
                ctx,
            )
        assert "issue_key" not in str(caught.value)

    async def test_a_nested_key_is_reported_by_its_path(self):
        ctx = {"n": {"ok": 1}}
        with pytest.raises(MappingResolutionError) as caught:
            await self.resolver.resolve_mapping(
                {"fields": {"summary": "{{$n.ok}}", "labels": "{{$n.missing}}"}},
                ctx,
            )
        assert [f.key for f in caught.value.failures] == ["fields.labels"]

    async def test_a_list_element_is_reported_by_its_index(self):
        ctx = {"n": {"ok": 1}}
        with pytest.raises(MappingResolutionError) as caught:
            await self.resolver.resolve_mapping(
                {"items": ["{{$n.ok}}", "{{$n.nope}}"]}, ctx
            )
        assert [f.key for f in caught.value.failures] == ["items[1]"]


class TestEveryFailureIsReportedAtOnce:
    """D3. Three broken keys must cost one run, not three."""

    def setup_method(self):
        self.resolver = VariableResolver([TraversalResolverStrategy()])

    async def test_all_three_appear_in_one_error(self):
        ctx = {"n": {"good": 1}}
        with pytest.raises(MappingResolutionError) as caught:
            await self.resolver.resolve_mapping(
                {
                    "a": "{{$n.missing_a}}",
                    "b": "{{$n.good}}",
                    "c": "{{$n.missing_c}}",
                    "d": "{{$missing_source.x}}",
                },
                ctx,
            )
        assert [f.key for f in caught.value.failures] == ["a", "c", "d"]
        assert caught.value.total_keys == 4

    async def test_the_report_is_bounded(self):
        """A generated node with 200 broken fields must not produce a 200-line
        error — it is stored on the task row and republished in an event."""
        ctx = {"n": {}}
        mapping = {f"k{i}": "{{$n.missing}}" for i in range(200)}
        with pytest.raises(MappingResolutionError) as caught:
            await self.resolver.resolve_mapping(mapping, ctx)

        assert len(caught.value.failures) == 10
        assert caught.value.dropped == 190
        assert "and 190 more" in str(caught.value)

    async def test_it_is_still_a_variable_resolution_error(self):
        """Every call site catches ``VariableResolutionError``. If this ever
        stops holding, resolution failures escape as unhandled 500s."""
        ctx = {"n": {}}
        with pytest.raises(VariableResolutionError):
            await self.resolver.resolve_mapping({"a": "{{$n.missing}}"}, ctx)


class TestNoPartialInputEscapes:
    """The load-bearing one.

    "Drop the bad key and carry on" is the tempting fix and the dangerous one:
    it is how a Jira comment reading "confidence {{$rootcause.metadata.score}}"
    reaches a customer's ticket. Missing input blocks the node.
    """

    def setup_method(self):
        self.resolver = VariableResolver([TraversalResolverStrategy()])

    async def test_a_partly_resolvable_mapping_yields_nothing(self):
        ctx = {"n": {"good": "real value"}}
        with pytest.raises(MappingResolutionError):
            await self.resolver.resolve_mapping(
                {"good": "{{$n.good}}", "bad": "{{$n.missing}}"}, ctx
            )

    async def test_a_fully_resolvable_mapping_is_unaffected(self):
        ctx = {"n": {"a": 1, "b": 2}}
        result = await self.resolver.resolve_mapping(
            {"a": "{{$n.a}}", "b": "{{$n.b}}", "lit": "static"}, ctx
        )
        assert result == {"a": 1, "b": 2, "lit": "static"}


class TestTheReportCarriesNoPayload:
    """The report is persisted on the task row and published in a NodeFailed
    event. Node input carries customer data; the report must carry none of it."""

    def setup_method(self):
        self.resolver = VariableResolver([TraversalResolverStrategy()])

    async def test_a_strict_coercion_failure_does_not_echo_the_value(self):
        secret = "ghp_" + "S" * 36
        ctx = {"n": {"token": secret, "missing_ok": 1}}
        with pytest.raises(MappingResolutionError) as caught:
            await self.resolver.resolve_mapping(
                {"count": "{{$n.token}}"}, ctx, type_map={"count": "number"},
            )
        message = str(caught.value)
        assert secret not in message
        assert "str" in message and "number" in message

    async def test_no_exception_object_is_retained(self):
        """Holding the exception would pin its traceback, and through it the
        run context and any materialized file content."""
        ctx = {"n": {}}
        with pytest.raises(MappingResolutionError) as caught:
            await self.resolver.resolve_mapping({"a": "{{$n.missing}}"}, ctx)

        (failure,) = caught.value.failures
        assert all(
            isinstance(getattr(failure, field), str)
            for field in ("key", "expression", "reason")
        )


class TestTheReportCannotCrashWhileBeingReported:
    """Found by rendering the real SA-2282 failure: the first draft separated
    key from expression with U+2190. Formatting it on a cp1252 stream raised
    UnicodeEncodeError — a crash *inside the error path*, which turns a precise
    diagnostic into a stack trace about encoding. The message is logged,
    persisted on the task row, and published in an event; every one of those
    sinks gets to choose its own codec.
    """

    def setup_method(self):
        self.resolver = VariableResolver([TraversalResolverStrategy()])

    async def test_the_message_survives_a_latin1_sink(self):
        ctx = {"n": {}}
        with pytest.raises(MappingResolutionError) as caught:
            await self.resolver.resolve_mapping(
                {"a": "{{$n.missing}}", "b": "{{$n.also_missing}}"}, ctx
            )
        str(caught.value).encode("cp1252")   # must not raise
        str(caught.value).encode("ascii")

    async def test_non_ascii_workflow_data_does_not_crash_the_report(self):
        """Our own separators are ASCII, but the author's data is not ours to
        choose — the underlying reason lists the keys that *were* available, and
        on this tenant those are routinely Vietnamese. The report must still be
        buildable; only our own decorations are constrained to ASCII."""
        ctx = {"n": {"nguyên_nhân": "sập nguồn"}}
        with pytest.raises(MappingResolutionError) as caught:
            await self.resolver.resolve_mapping({"body": "{{$n.missing}}"}, ctx)

        message = str(caught.value)
        assert "nguyên_nhân" in message
        message.encode("utf-8")   # the sink CP actually uses


class TestKnownLimits:
    def setup_method(self):
        self.resolver = VariableResolver([TraversalResolverStrategy()])

    async def test_one_field_reports_its_first_broken_token_only(self):
        """Documented, not accidental. D3 collects across *fields*; within a
        single template ``resolve`` still stops at the first failure, so a body
        with two broken tokens names one. Fixing the named one and re-running
        surfaces the next — acceptable, because the field is already identified
        and the author is looking straight at it.
        """
        ctx = {"n": {}}
        with pytest.raises(MappingResolutionError) as caught:
            await self.resolver.resolve_mapping(
                {"body": "{{$n.first_bad}} and {{$n.second_bad}}"}, ctx
            )
        (failure,) = caught.value.failures
        assert failure.expression == "{{$n.first_bad}}"
