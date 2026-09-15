"""COMPUTE node handler: safe inline math/expression evaluator."""

from __future__ import annotations

import ast
import json
import logging
import operator

from app.layer1_domain.entities.data_reference import is_file_ref
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer1_domain.node_spec.specs import ComputeConfig
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer2_application.node_handlers.inline_bases import (
    AsyncInlineHandlerBase,
)
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    VariableResolver,
    parse_expression,
    find_all_expressions,
)


class FileRefView:
    """Count-facade over a file-backed reference for COMPUTE.

    ``len(view)`` returns the ref's ``row_count`` and ``bool(view)`` its
    has-data flag — both **without downloading** the CSV. This is what lets
    ``len({{$node.items}})`` return the row count instead of the number of
    keys in the stringified pointer dict (SA-1905 D2).

    It deliberately supports only ``len``/truthiness. Element access
    (iteration, indexing, ``sum``) would need to materialize rows, which the
    lazy per-row OData view (D2.4) provides; here that raises a clear error
    rather than silently downloading a 500MB array into a COMPUTE node.
    """

    __slots__ = ("_ref",)

    def __init__(self, ref: dict) -> None:
        self._ref = ref

    def __len__(self) -> int:
        return int(self._ref.get("row_count") or 0)

    def __bool__(self) -> bool:
        return bool(
            (self._ref.get("row_count") or 0)
            or self._ref.get("artifacts")
            or self._ref.get("columns")
        )

    def __iter__(self):
        raise ComputeExpressionError(
            "cannot iterate a file-backed collection inside COMPUTE; use a "
            "LOOP node, or len()/a row-count for aggregate math"
        )


_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_ALLOWED_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_ALLOWED_CMPOPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}

_ALLOWED_BOOLOPS = {
    ast.And: all,
    ast.Or: any,
}

_SAFE_BUILTINS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "len": len,
    "int": int,
    "float": float,
    "str": str,
    # JSON-style boolean/null names (from json.dumps interpolation)
    "true": True,
    "false": False,
    "null": None,
}


class ComputeExpressionError(Exception):
    pass


# Sentinel: the whole expression is a single file-backed reference — pass the
# pointer through as the result instead of trying to evaluate it.
_NO_DIRECT = object()
_FILE_REF_TRACE = "<file-backed reference>"


def _safe_eval(expr: str, names: dict | None = None) -> object:
    """Evaluate a whitelisted expression.

    ``names`` optionally binds identifiers to pre-resolved Python objects so
    a caller (the COMPUTE handler) can evaluate ``len(x)`` against a real
    object instead of a stringified value — this is how a file-backed
    collection's ``len``/truthiness reads its ``row_count`` facade without a
    download (SA-1905 D2). Omitted → identifiers resolve only to the safe
    builtins, i.e. byte-identical to the pre-existing behaviour.
    """
    names = names or {}
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as exc:
        raise ComputeExpressionError(f"Invalid expression syntax: {exc}") from exc
    return _eval_node(tree.body, names)


def _eval_node(node: ast.AST, names: dict) -> object:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, str, bool, type(None))):
            return node.value
        raise ComputeExpressionError(f"Unsupported constant type: {type(node.value).__name__}")
    if isinstance(node, ast.BinOp):
        op_func = _ALLOWED_BINOPS.get(type(node.op))
        if op_func is None:
            raise ComputeExpressionError(f"Unsupported binary operator: {type(node.op).__name__}")
        left = _eval_node(node.left, names)
        right = _eval_node(node.right, names)
        try:
            return op_func(left, right)
        except Exception as exc:
            raise ComputeExpressionError(f"Evaluation error: {exc}") from exc
    if isinstance(node, ast.UnaryOp):
        op_func = _ALLOWED_UNARYOPS.get(type(node.op))
        if op_func is None:
            raise ComputeExpressionError(f"Unsupported unary operator: {type(node.op).__name__}")
        return op_func(_eval_node(node.operand, names))
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, names)
        for op, comparator in zip(node.ops, node.comparators):
            op_func = _ALLOWED_CMPOPS.get(type(op))
            if op_func is None:
                raise ComputeExpressionError(f"Unsupported comparison: {type(op).__name__}")
            right = _eval_node(comparator, names)
            if not op_func(left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.BoolOp):
        reducer = _ALLOWED_BOOLOPS.get(type(node.op))
        if reducer is None:
            raise ComputeExpressionError(f"Unsupported boolean operator: {type(node.op).__name__}")
        return reducer(_eval_node(v, names) for v in node.values)
    if isinstance(node, ast.IfExp):
        return _eval_node(node.body, names) if _eval_node(node.test, names) else _eval_node(node.orelse, names)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ComputeExpressionError("Only simple function calls are allowed")
        func_name = node.func.id
        func = _SAFE_BUILTINS.get(func_name)
        if func is None:
            raise ComputeExpressionError(f"Function not allowed: {func_name}")
        args = [_eval_node(a, names) for a in node.args]
        try:
            return func(*args)
        except Exception as exc:
            raise ComputeExpressionError(f"Function call error: {exc}") from exc
    if isinstance(node, ast.Name):
        if node.id in names:
            return names[node.id]
        if node.id in _SAFE_BUILTINS:
            return _SAFE_BUILTINS[node.id]
        raise ComputeExpressionError(f"Name not allowed: {node.id}")
    if isinstance(node, ast.List):
        return [_eval_node(el, names) for el in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(el, names) for el in node.elts)
    if isinstance(node, ast.Dict):
        keys = [_eval_node(k, names) for k in node.keys]
        values = [_eval_node(v, names) for v in node.values]
        return dict(zip(keys, values))
    raise ComputeExpressionError(f"Unsupported expression element: {type(node).__name__}")


class ComputeNodeHandler(AsyncInlineHandlerBase):
    def __init__(self, variable_resolver: VariableResolver) -> None:
        self._resolver = variable_resolver

    async def handle(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        workflow: Workflow,
        topology: GraphTopology,
        completed_task: Task | None,
        task_factory: TaskFactory,
    ) -> NodeHandlerResult:
        result = NodeHandlerResult()

        # Phase 6 — uniform execution fields. COMPUTE always completes on the
        # ``success`` port. Evaluation errors are DELIBERATELY soft (SA-1752):
        # the node still completes on ``success`` with a
        # ``{"result": {"_error": true, "message": ...}}`` payload so a
        # downstream CONDITION can branch on ``…output.result._error``. Only
        # *template-resolution* failure (the resolve() call below, outside the
        # try) fails the task loud. See docs/design/compute-node-usage.md
        # §"Error behavior" and tests/unit/test_compute_handler.py
        # ::test_eval_error_is_soft — that contract is intentional.
        # NB: the ``failure`` out-port the spec declares is intentionally never
        # emitted today; do NOT wire a ``failure`` edge expecting evaluation
        # errors to take it.
        result.output_port = "success"
        result.completion_mode = "immediate"

        cfg = ComputeConfig.from_node(node)
        expression = cfg.expression
        # Resolve templates to an eval-ready (text, bound-objects, direct)
        # triple. A whole file-backed value passes through as a pointer; an
        # embedded file_ref is bound as a FileRefView so len()/truthiness read
        # its row_count without a download (SA-1905 D2). Non-file_ref
        # expressions take the legacy string-substitution path unchanged. This
        # call is OUTSIDE the try so template-resolution failure fails loud
        # (the documented contract), same as before.
        eval_text, eval_names, direct = await self._resolve_for_compute(
            expression, run.context.variables,
        )
        if direct is not _NO_DIRECT:
            computed_value = direct
            resolved_str = _FILE_REF_TRACE
        else:
            resolved_str = eval_text
            try:
                computed_value = _safe_eval(eval_text, eval_names)
            except ComputeExpressionError as exc:
                logging.getLogger(__name__).warning(
                    "Compute expression failed for node %s: %s (expression: %s)",
                    node.id, exc, resolved_str,
                )
                computed_value = {"_error": True, "message": str(exc)}
        result.state_updates[node.id] = {
            # ``expression`` / ``resolved`` ride in the output (not only
            # metadata) so the evaluation trace shows in the node's
            # output panel next to the result.
            "output": {
                "result": computed_value,
                "expression": expression,
                "resolved": resolved_str,
            },
            "metadata": {
                "type": "compute",
                "expression": expression,
                "resolved": resolved_str,
            },
        }
        result_key = cfg.result_key
        if result_key:
            result.state_updates[result_key] = computed_value
        return result

    async def _resolve_for_compute(
        self, expression: object, context: dict,
    ) -> tuple[str, dict, object]:
        """Resolve template expressions to ``(eval_text, names, direct)``.

        * **Full-match single expression** — ``{{$node.items}}``:
            * a file_ref → ``("", {}, file_ref)``: pass the pointer through as
              the result (a whole file-backed value can't fit in a COMPUTE
              output; previously ``str(ref)`` round-tripped through an ast
              literal, which is fragile).
            * otherwise → ``(str(value), {}, _NO_DIRECT)`` — byte-identical to
              the legacy ``str(await resolve(...))``.
        * **Embedded expressions** — ``len({{$node.items}}) * 2``:
            * none resolve to a file_ref → the exact legacy ``json.dumps`` /
              ``str`` substitution (common path, no behaviour change).
            * some do → bind each resolved value as a generated name (file_refs
              wrapped in :class:`FileRefView`) so ``len``/truthiness are
              correct without materializing the array.
        * **No expressions** → ``(expression, {}, _NO_DIRECT)``.
        """
        parsed = parse_expression(expression) if isinstance(expression, str) else None
        if parsed is not None:
            value = await self._resolver.resolve(expression, context)
            if is_file_ref(value):
                return "", {}, value
            return str(value), {}, _NO_DIRECT
        if not isinstance(expression, str):
            return expression, {}, _NO_DIRECT
        exprs = find_all_expressions(expression)
        if not exprs:
            return expression, {}, _NO_DIRECT
        values = [await self._resolver.resolve(e.raw, context) for e in exprs]
        if not any(is_file_ref(v) for v in values):
            text = expression
            for e, v in zip(exprs, values):
                rep = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                text = text.replace(e.raw, rep)
            return text, {}, _NO_DIRECT
        text = expression
        names: dict = {}
        for i, (e, v) in enumerate(zip(exprs, values)):
            name = f"__cv{i}"
            names[name] = FileRefView(v) if is_file_ref(v) else v
            text = text.replace(e.raw, name)
        return text, names, _NO_DIRECT
