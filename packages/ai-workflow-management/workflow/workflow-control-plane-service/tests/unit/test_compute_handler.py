"""COMPUTE node capability tests.

Pins the full capability surface of the COMPUTE node:

* ``_safe_eval`` — the AST-whitelist expression evaluator (operators,
  functions, literals, error cases, security rejections).
* ``ComputeNodeHandler`` — template resolution before evaluation, output
  shape, ``result_key``, soft-error behavior.

These tests are the executable contract behind
``docs/design/compute-node-usage.md`` — update both together.
"""

from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import (
    GraphTopology,
    RunContext,
    WorkflowRun,
)
from app.layer1_domain.value_objects import NodeType
from app.layer2_application.node_handlers.compute_handler import (
    ComputeExpressionError,
    ComputeNodeHandler,
    _safe_eval,
)
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    TraversalResolverStrategy,
    VariableResolver,
)


# ════════════════════════════════ _safe_eval ════════════════════════════════


class TestArithmetic:
    @pytest.mark.parametrize("expr,expected", [
        ("1 + 2", 3),
        ("7 - 10", -3),
        ("6 * 7", 42),
        ("10 / 4", 2.5),          # true division
        ("10 // 4", 2),           # floor division
        ("10 % 4", 2),            # modulo
        ("2 ** 10", 1024),        # power
        ("-5 + 2", -3),           # unary minus
        ("+5", 5),                # unary plus
        ("2 + 3 * 4", 14),        # precedence
        ("(2 + 3) * 4", 20),      # parentheses
        ("0.1 + 0.2", 0.1 + 0.2),  # float arithmetic
    ])
    def test_arithmetic(self, expr, expected):
        assert _safe_eval(expr) == expected

    def test_division_by_zero_is_expression_error(self):
        with pytest.raises(ComputeExpressionError):
            _safe_eval("1 / 0")


class TestComparisonsAndBoolean:
    @pytest.mark.parametrize("expr,expected", [
        ("1 == 1", True),
        ("1 != 2", True),
        ("1 < 2", True),
        ("2 <= 2", True),
        ("3 > 2", True),
        ("3 >= 4", False),
        ("1 < 2 < 3", True),       # chained comparison
        ("1 < 2 > 5", False),
        ("'a' == 'a'", True),      # string comparison
    ])
    def test_comparisons(self, expr, expected):
        assert _safe_eval(expr) is expected

    @pytest.mark.parametrize("expr,expected", [
        ("True and False", False),
        ("True or False", True),
        # CONTRACT: and/or reduce via all()/any() — they return a BOOL,
        # not the last operand like Python (``1 and 2`` is True, not 2).
        ("1 and 2", True),
        ("0 or 3", True),
        ("0 and 1", False),
        ("0 or 0", False),
    ])
    def test_boolean_ops_return_bool(self, expr, expected):
        assert _safe_eval(expr) is expected

    def test_not_is_unsupported(self):
        # ``not`` is NOT whitelisted; use ``== False`` or a ternary instead.
        with pytest.raises(ComputeExpressionError):
            _safe_eval("not True")

    def test_ternary_conditional(self):
        assert _safe_eval("'high' if 10 > 5 else 'low'") == "high"
        assert _safe_eval("1 if False else 2") == 2


class TestFunctions:
    @pytest.mark.parametrize("expr,expected", [
        ("abs(-7)", 7),
        ("round(2.567, 2)", 2.57),
        ("round(2.5)", 2),
        ("min(3, 1, 2)", 1),
        ("max([4, 9, 2])", 9),
        ("sum([1, 2, 3])", 6),
        ("len('abcd')", 4),
        ("len([1, 2, 3])", 3),
        ("int('42')", 42),
        ("float('0.5')", 0.5),
        ("str(42)", "42"),
        ("int('42') + float('0.5')", 42.5),
        ("max(abs(-3), min(10, 5))", 5),   # nested calls
    ])
    def test_safe_builtins(self, expr, expected):
        assert _safe_eval(expr) == expected

    @pytest.mark.parametrize("expr", [
        "eval('1')",
        "open('/etc/passwd')",
        "__import__('os')",
        "exec('x = 1')",
        "getattr(1, 'bit_length')",
    ])
    def test_non_whitelisted_functions_rejected(self, expr):
        with pytest.raises(ComputeExpressionError):
            _safe_eval(expr)

    def test_function_argument_error_is_expression_error(self):
        with pytest.raises(ComputeExpressionError):
            _safe_eval("int('not-a-number')")


class TestLiterals:
    @pytest.mark.parametrize("expr,expected", [
        ("42", 42),
        ("'hello'", "hello"),
        ("True", True),
        ("False", False),
        ("None", None),
        # JSON-style names (from json.dumps interpolation of variables)
        ("true", True),
        ("false", False),
        ("null", None),
        ("[1, 2, 3]", [1, 2, 3]),
        ("(1, 2)", (1, 2)),
        ("{'a': 1, 'b': 2}", {"a": 1, "b": 2}),
        ("[1, 2] + [3]", [1, 2, 3]),       # list concatenation
        ("'ab' + 'cd'", "abcd"),           # string concatenation
        ("'ab' * 2", "abab"),              # string repetition
    ])
    def test_literals(self, expr, expected):
        assert _safe_eval(expr) == expected


class TestSecurityRejections:
    @pytest.mark.parametrize("expr", [
        "(1).bit_length()",        # attribute access / method call
        "[1, 2][0]",               # subscript
        "x",                       # unknown name
        "[i for i in [1]]",        # comprehension
        "lambda: 1",               # lambda
        "f'{1}'",                  # f-string
        "1; 2",                    # statements
        "",                        # empty expression
        "1 +",                     # syntax error
    ])
    def test_rejected_constructs(self, expr):
        with pytest.raises(ComputeExpressionError):
            _safe_eval(expr)


# ═══════════════════════════ ComputeNodeHandler ═════════════════════════════


@pytest.fixture
def resolver():
    return VariableResolver(strategies=[TraversalResolverStrategy()])


@pytest.fixture
def handler(resolver):
    return ComputeNodeHandler(resolver)


@pytest.fixture
def run():
    return WorkflowRun(
        id="run-1",
        workflow_id="wf-1",
        context=RunContext(
            variables={
                "upstream": {
                    "output": {
                        "n": 7,
                        "price": 19.99,
                        "qty": 3,
                        "name": "hello",
                        "items": [1, 2, 3],
                        "flag": True,
                    },
                },
            },
        ),
    )


def _node(expression: str, **config) -> WorkflowNode:
    return WorkflowNode(
        id="compute-1",
        name="calc",
        node_type=NodeType.COMPUTE,
        config={"expression": expression, **config},
    )


async def _run_handler(handler, node, run):
    workflow = Workflow(id="wf-1", name="T", nodes=[node], edges=[])
    return await handler.handle(
        node=node, run=run, workflow=workflow, topology=GraphTopology(),
        completed_task=None, task_factory=TaskFactory(),
    )


def _result_of(handler_result):
    return handler_result.state_updates["compute-1"]["output"]["result"]


class TestHandlerResolution:
    @pytest.mark.asyncio
    async def test_literal_expression(self, handler, run):
        result = await _run_handler(handler, _node("21 + 21"), run)
        assert _result_of(result) == 42

    @pytest.mark.asyncio
    async def test_full_match_template_resolves_native(self, handler, run):
        result = await _run_handler(handler, _node("{{$upstream.output.n}}"), run)
        assert _result_of(result) == 7

    @pytest.mark.asyncio
    async def test_embedded_numeric_templates(self, handler, run):
        result = await _run_handler(
            handler,
            _node("{{$upstream.output.price}} * {{$upstream.output.qty}}"),
            run,
        )
        assert _result_of(result) == pytest.approx(59.97)

    @pytest.mark.asyncio
    async def test_boolean_logic_over_templates(self, handler, run):
        result = await _run_handler(
            handler,
            _node("{{$upstream.output.flag}} and {{$upstream.output.n}} > 5"),
            run,
        )
        assert _result_of(result) is True

    @pytest.mark.asyncio
    async def test_list_template_with_function(self, handler, run):
        result = await _run_handler(
            handler, _node("sum({{$upstream.output.items}})"), run,
        )
        assert _result_of(result) == 6

    @pytest.mark.asyncio
    async def test_string_template_must_be_quoted(self, handler, run):
        # CONTRACT: string variables interpolate WITHOUT quotes — the
        # expression author must quote them, otherwise the bare word is
        # parsed as a (disallowed) name and produces a soft error.
        ok = await _run_handler(
            handler, _node('"{{$upstream.output.name}}" == "hello"'), run,
        )
        assert _result_of(ok) is True

        bare = await _run_handler(
            handler, _node("{{$upstream.output.name}}"), run,
        )
        err = _result_of(bare)
        assert isinstance(err, dict) and err.get("_error") is True

    @pytest.mark.asyncio
    async def test_ternary_over_template(self, handler, run):
        result = await _run_handler(
            handler,
            _node("'bulk' if {{$upstream.output.qty}} >= 3 else 'single'"),
            run,
        )
        assert _result_of(result) == "bulk"


class TestHandlerContract:
    @pytest.mark.asyncio
    async def test_output_shape_and_metadata(self, handler, run):
        result = await _run_handler(handler, _node("1 + 1"), run)
        entry = result.state_updates["compute-1"]
        # expression/resolved ride in the output so the evaluation trace
        # shows in the node's output panel next to the result.
        assert entry["output"] == {
            "result": 2,
            "expression": "1 + 1",
            "resolved": "1 + 1",
        }
        assert entry["metadata"]["type"] == "compute"
        assert entry["metadata"]["expression"] == "1 + 1"
        assert entry["metadata"]["resolved"] == "1 + 1"

    @pytest.mark.asyncio
    async def test_routing_fields(self, handler, run):
        result = await _run_handler(handler, _node("1"), run)
        assert result.output_port == "success"
        assert result.completion_mode == "immediate"
        assert result.tasks_to_dispatch == []

    @pytest.mark.asyncio
    async def test_result_key_adds_top_level_variable(self, handler, run):
        result = await _run_handler(
            handler, _node("6 * 7", result_key="answer"), run,
        )
        assert _result_of(result) == 42
        assert result.state_updates["answer"] == 42

    @pytest.mark.asyncio
    async def test_eval_error_is_soft(self, handler, run):
        # Evaluation failures do NOT raise — the node completes with an
        # ``{"_error": true, "message": ...}`` result so downstream
        # CONDITION nodes can route on it.
        result = await _run_handler(handler, _node("1 / 0"), run)
        err = _result_of(result)
        assert err["_error"] is True
        assert "message" in err
        assert result.completion_mode == "immediate"
        assert result.output_port == "success"

    @pytest.mark.asyncio
    async def test_empty_expression_is_soft_error(self, handler, run):
        result = await _run_handler(handler, _node(""), run)
        assert _result_of(result)["_error"] is True

    @pytest.mark.asyncio
    async def test_metadata_records_resolved_expression(self, handler, run):
        result = await _run_handler(
            handler, _node("{{$upstream.output.n}} * 3"), run,
        )
        entry = result.state_updates["compute-1"]
        assert entry["metadata"]["expression"] == "{{$upstream.output.n}} * 3"
        assert entry["metadata"]["resolved"] == "7 * 3"
        assert entry["output"]["result"] == 21

    @pytest.mark.asyncio
    async def test_output_records_resolved_expression(self, handler, run):
        result = await _run_handler(
            handler, _node("{{$upstream.output.n}} * 3"), run,
        )
        out = result.state_updates["compute-1"]["output"]
        assert out["expression"] == "{{$upstream.output.n}} * 3"
        assert out["resolved"] == "7 * 3"


# ═══════════ SA-1905 D2 — COMPUTE over a file-backed collection ═══════════
# len({{$node.items}}) must return the ROW COUNT, not the number of keys in
# the file_ref pointer dict. COMPUTE stringified the resolved value, so a
# file_ref became json.dumps({"__file_ref": true, ...}) → len(dict)=~7. The
# fix binds resolved expressions as objects for _safe_eval and wraps a
# file_ref in a count-facade whose __len__ is row_count (no download).


def _cfile_ref(row_count: int, **extra) -> dict:
    return {
        "__file_ref": True, "file_id": "fx", "content_type": "text/csv",
        "row_count": row_count, "columns": [], "column_types": {},
        "artifacts": [], **extra,
    }


def _run_with(items_ref, source="n", field="items"):
    return WorkflowRun(
        id="r", workflow_id="w",
        context=RunContext(variables={source: {field: items_ref}}),
    )


class TestHandlerFileBackedCollection:
    @pytest.mark.asyncio
    async def test_len_over_file_backed_array_is_row_count(self, handler):
        result = await _run_handler(handler, _node("len({{$n.items}})"), _run_with(_cfile_ref(3)))
        assert _result_of(result) == 3

    @pytest.mark.asyncio
    async def test_len_arithmetic_over_file_backed(self, handler):
        result = await _run_handler(handler, _node("len({{$n.items}}) * 2"), _run_with(_cfile_ref(10)))
        assert _result_of(result) == 20

    @pytest.mark.asyncio
    async def test_full_match_file_backed_is_pointer_passthrough(self, handler):
        ref = _cfile_ref(5)
        result = await _run_handler(handler, _node("{{$n.items}}"), _run_with(ref))
        # A whole file-backed value can't fit in a COMPUTE result; pass the
        # pointer through (previously str(ref) → _safe_eval → soft error).
        assert _result_of(result) == ref

    @pytest.mark.asyncio
    async def test_inline_list_len_still_works(self, handler, run):
        # Regression: inline arrays keep the legacy json.dumps path.
        result = await _run_handler(handler, _node("len({{$upstream.output.items}})"), run)
        assert _result_of(result) == 3
