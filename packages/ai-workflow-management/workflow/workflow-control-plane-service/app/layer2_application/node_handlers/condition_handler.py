"""CONDITION node handler: binary branching based on boolean expression."""

from __future__ import annotations

import logging
from typing import Any, Optional
from collections.abc import Callable

from app.layer1_domain.entities.workflow import WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.exceptions import ExpressionError
from app.layer1_domain.node_spec.specs import ConditionNodeConfig
from app.layer2_application.node_handlers.base_node_handler import RoutingDecision
from app.layer2_application.node_handlers.routing_base import RoutingHandlerBase
from app.layer1_domain.value_objects.comparison import Comparison
from app.layer2_application.resolve.variable_resolver import (
    ConditionEvaluator,
    VariableResolutionError,
)

logger = logging.getLogger(__name__)


def _format_rules(rules: list) -> str:
    """Render rule dicts as ``"<key> <op> <value>"`` joined with `` AND ``."""
    parts = []
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        key = rule.get("key")
        if key is None:
            continue
        op = rule.get("operations", "==")
        value = rule.get("value")
        parts.append(f"{key} {op}" if value is None else f"{key} {op} {value}")
    return " AND ".join(parts)


class ConditionNodeHandler(RoutingHandlerBase):
    """Routes downstream traffic on a boolean expression.

    Two declared input styles (exactly one may be set — enforced at
    save time via the spec's ``requires_one_of``):

    * **Structured** — ``config.data`` carries a list of
      ``{key, operations, value}`` rules ANDed together. The unresolved
      ``key`` text is preserved in the ``NodeCompleted.expression``
      field for observer trace fidelity.
    * **Expression** — ``config.expression`` is a single boolean
      expression string passed straight to :class:`ConditionEvaluator`.

    ``select_port`` returns a :class:`RoutingDecision` whose
    ``edge_accepts`` closure walks the historical edge-match heuristic
    (``edge.condition`` text or ``edge.source_port_id`` label) so the
    base populates ``taken_edge_ports`` in a single pass.
    """

    METADATA_TYPE = "condition"

    def __init__(self, condition_evaluator: ConditionEvaluator) -> None:
        self._evaluator = condition_evaluator

    async def select_port(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
    ) -> RoutingDecision:
        evaluation = await self._evaluate(node, run)
        condition_result = evaluation["result"]
        branch = "then" if condition_result else "else"
        completion_payload = {
            "branch": branch,
            "expression": evaluation["payload_expression"],
            "condition_result": condition_result,
        }
        return RoutingDecision(
            matched_branch=branch,
            matched_port_id="",
            completion_payload=completion_payload,
            edge_accepts=self._build_edge_accepts(condition_result),
            output={
                "result": condition_result,
                "branch": branch,
                "expression": evaluation["expression"],
                "resolved": evaluation["resolved"],
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_edge_accepts(
        condition_result: bool,
    ) -> Callable[[Any], Optional[str]]:
        """Return the edge-acceptance closure for the base template."""

        def accepts(edge: Any) -> Optional[str]:
            edge_condition = (edge.condition or "").lower()
            port_id = (edge.source_port_id or "").lower()
            is_then = (
                edge_condition in ("then", "true")
                or port_id in ("true_branch", "then", "true")
                or (not edge_condition and not port_id)  # default: treat as "then"
            )
            is_else = (
                edge_condition in ("else", "false")
                or port_id in ("false_branch", "else", "false")
            )
            if (condition_result and is_then) or (not condition_result and is_else):
                return (
                    edge.source_port_id
                    or ("true_branch" if condition_result else "false_branch")
                )
            return None

        return accepts

    async def _evaluate(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
    ) -> dict[str, Any]:
        """Evaluate the condition and return the full trace.

        Returns ``{result, payload_expression, expression, resolved}``:

        * ``result`` — the boolean outcome.
        * ``payload_expression`` — legacy ``NodeCompleted.expression``
          value (unchanged semantics: resolved rule list repr for
          structured mode, raw expression string for legacy mode).
        * ``expression`` — what the author wrote: raw ``"key op value"``
          rules joined with `` AND `` (structured) or the raw
          ``config['expression']`` string (legacy).
        * ``resolved`` — the same text after variable substitution, for
          the node's output panel.

        Structured rules take precedence; falls back to the legacy
        ``config['expression']`` string when no structured data is
        bound.
        """
        cfg = ConditionNodeConfig.from_node(node)
        structured_data = await self._resolve_structured_conditions(cfg, run)
        if structured_data is not None:
            result = await self._evaluate_structured(
                structured_data, run.context.variables,
            )
            raw_data = cfg.data
            raw_rules = (
                list(raw_data) if isinstance(raw_data, (list, tuple))
                else structured_data
            )
            return {
                "result": result,
                "payload_expression": str(structured_data),
                "expression": _format_rules(raw_rules),
                "resolved": _format_rules(structured_data),
            }

        expression = cfg.expression
        try:
            result = await self._evaluator.evaluate(
                expression, run.context.variables,
            )
        except VariableResolutionError as exc:
            # SA-1753: a condition that cannot resolve its expression must
            # fail LOUD (task FAILED) rather than silently routing ``else``.
            # Routing on unresolved data silently runs the wrong branch — a
            # correctness hazard. This matches COMPUTE's resolution-error
            # contract (template resolution failure fails the task) and the
            # repo's fallback-ban doctrine.
            raise ExpressionError(
                f"CONDITION node {node.id}: could not resolve/evaluate "
                f"expression {expression!r}: {exc}"
            ) from exc
        try:
            resolved = str(await self._evaluator._resolver.resolve(
                expression, run.context.variables,
            ))
        except VariableResolutionError:
            resolved = expression
        return {
            "result": result,
            "payload_expression": expression,
            "expression": expression,
            "resolved": resolved,
        }

    async def _resolve_structured_conditions(
        self,
        cfg: ConditionNodeConfig,
        run: WorkflowRun,
    ) -> list[dict] | None:
        """Extract structured condition rules from ``config.data``.

        Returns the rule list (each ``{key, operations, value}``), or
        ``None`` when no structured rules are configured.
        """
        data = cfg.data
        if data is None or data == () or data == []:
            return None
        if isinstance(data, str):
            try:
                resolved = await self._evaluator._resolver.resolve(
                    data, run.context.variables,
                )
                if isinstance(resolved, list):
                    return resolved
            except VariableResolutionError:
                pass
            return None
        if isinstance(data, (list, tuple)):
            resolved_rules: list[dict] = []
            for rule in data:
                if not isinstance(rule, dict):
                    continue
                resolved_rule: dict[str, Any] = {}
                for k, v in rule.items():
                    if isinstance(v, str):
                        try:
                            resolved_rule[k] = await self._evaluator._resolver.resolve(
                                v, run.context.variables,
                            )
                        except VariableResolutionError:
                            resolved_rule[k] = v
                    else:
                        resolved_rule[k] = v
                resolved_rules.append(resolved_rule)
            return resolved_rules if resolved_rules else None
        return None

    async def _evaluate_structured(
        self,
        rules: list[dict],
        context: dict,
    ) -> bool:
        """Evaluate ``rules`` ANDed together. Empty list → True."""
        if not rules:
            return True
        for rule in rules:
            key_val = rule.get("key")
            op = rule.get("operations", "=")
            expected = rule.get("value")
            if isinstance(key_val, str):
                try:
                    key_val = await self._evaluator._resolver.resolve(key_val, context)
                except VariableResolutionError as exc:
                    # SA-1753: fail loud on an unresolvable structured-rule key
                    # instead of silently evaluating the whole condition False
                    # (which routes the wrong branch). Consistent with the
                    # legacy-expression path above.
                    raise ExpressionError(
                        f"CONDITION: could not resolve rule key {key_val!r}: {exc}"
                    ) from exc
            if not Comparison.evaluate(key_val, op, expected):
                return False
        return True
