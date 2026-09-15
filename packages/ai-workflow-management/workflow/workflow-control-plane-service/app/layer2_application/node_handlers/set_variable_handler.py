"""SET_VARIABLE node handler: writes name-keyed mutable state into ``__vars``.

Variables set here are visible to downstream nodes via ``{{$variables.<name>}}``
(resolved by :class:`SystemVariablesFrame`). Parallel writes of the same name
are rejected at workflow-save time by the DAG validator, so this handler
treats the namespace as conflict-free at runtime.

Each declaration carries a ``type`` (one of ``string``/``number``/``boolean``/
``object``/``array``) plus an optional ``item_type`` for ``array``. The
resolved value is best-effort coerced to the declared type; on impossible
coercion the raw resolved value is stored as-is (pass-through). This matches
the existing ``_coerce_config_value`` pattern used by the workflow entity.
"""

from __future__ import annotations

import json
from typing import Any

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer1_domain.node_spec.specs import SetVariableConfig
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer2_application.node_handlers.inline_bases import (
    AsyncInlineHandlerBase,
)
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import VariableResolver


_VARS_SLOT = "__vars"

_PRIMITIVE_TYPES = ("string", "number", "boolean", "object")
_VALID_TYPES = (*_PRIMITIVE_TYPES, "array")


def _coerce_primitive(value: Any, target: str) -> Any:
    """Coerce ``value`` to a primitive ``target`` type (``string``/``number``/
    ``boolean``/``object``). Returns the original value on impossible
    coercion. Mirrors the pass-through semantics of
    :func:`app.layer1_domain.entities.workflow._coerce_config_value`.
    """
    if value is None:
        return None
    try:
        if target == "string":
            if isinstance(value, str):
                return value
            if isinstance(value, (dict, list)):
                return json.dumps(value)
            return str(value)
        if target == "number":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return value
            f = float(str(value))
            return int(f) if f == int(f) else f
        if target == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                low = value.strip().lower()
                if low in ("true", "1", "yes"):
                    return True
                if low in ("false", "0", "no", ""):
                    return False
            if isinstance(value, (int, float)):
                return bool(value)
        if target == "object":
            if isinstance(value, dict):
                return value
            if isinstance(value, str):
                stripped = value.strip()
                if stripped.startswith("{"):
                    parsed = json.loads(stripped)
                    if isinstance(parsed, dict):
                        return parsed
    except (ValueError, TypeError):
        pass
    return value


def _coerce_variable(value: Any, declared_type: str, item_type: str | None) -> Any:
    """Coerce ``value`` to ``declared_type`` (with optional array
    ``item_type``). Pass-through on failure: never raises.
    """
    if not declared_type or declared_type not in _VALID_TYPES:
        return value
    if value is None:
        return None
    if declared_type != "array":
        return _coerce_primitive(value, declared_type)

    items: list[Any]
    if isinstance(value, list):
        items = list(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                items = parsed if isinstance(parsed, list) else [value]
            except (ValueError, TypeError):
                items = [value]
        else:
            items = [value]
    else:
        items = [value]

    if item_type and item_type in _PRIMITIVE_TYPES:
        items = [_coerce_primitive(it, item_type) for it in items]
    return items


class SetVariableHandler(AsyncInlineHandlerBase):
    """Resolves each declared (name, value_expression) pair and writes it
    into ``run.context.variables["__vars"][name]``.

    Config shape on the node::

        config.variables: list[{"name": str, "value_expression": Any}]

    The value can be a literal (number, string, dict) or a
    ``{{$source.path}}`` expression — both go through the resolver, so
    references to upstream nodes / metadata / other variables work.
    """

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
        result.output_port = "out_default"
        result.completion_mode = "immediate"

        declarations = SetVariableConfig.from_node(node).variables or ()
        if not isinstance(declarations, (list, tuple)):
            declarations = ()

        # Read-modify-write: the projector does shallow ``update()`` of
        # top-level keys in run.context.variables. To preserve previously-
        # set variables we must emit the FULL merged ``__vars`` dict.
        existing = (run.context.variables or {}).get(_VARS_SLOT) or {}
        merged: dict[str, Any] = dict(existing)

        for decl in declarations:
            if not isinstance(decl, dict):
                continue
            name = decl.get("name")
            if not name or not isinstance(name, str):
                continue
            raw = decl.get("value_expression")
            resolved = await self._resolver.resolve(raw, run.context.variables) if isinstance(raw, str) else raw
            declared_type = decl.get("type")
            item_type = decl.get("item_type")
            if isinstance(declared_type, str):
                resolved = _coerce_variable(
                    resolved, declared_type,
                    item_type if isinstance(item_type, str) else None,
                )
            merged[name] = resolved

        result.state_updates[_VARS_SLOT] = merged
        result.state_updates[node.id] = {
            "metadata": {
                "type": "set_variable",
                "names": [d.get("name") for d in declarations if isinstance(d, dict)],
            },
        }
        return result
