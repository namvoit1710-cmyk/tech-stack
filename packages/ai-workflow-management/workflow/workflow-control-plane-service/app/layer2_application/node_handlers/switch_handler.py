"""SWITCH node handler: multi-way routing based on expression value matching."""

from __future__ import annotations

import logging
from typing import Any, Optional
from collections.abc import Callable

from app.layer1_domain.entities.workflow import WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer1_domain.node_spec.specs import SwitchNodeConfig
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer2_application.node_handlers.base_node_handler import RoutingDecision
from app.layer2_application.node_handlers.routing_base import RoutingHandlerBase
from app.layer1_domain.value_objects.comparison import Comparison
from app.layer2_application.resolve.variable_resolver import (
    VariableResolutionError,
    VariableResolver,
)
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow

logger = logging.getLogger(__name__)


def _format_rule_expression(key_raw: Any, op_raw: Any, expected_raw: Any) -> str:
    """Format a switch rule as a ``"<key> <op> <value>"`` comparison string,
    using the *unresolved* templates so observers see the original expression
    text the workflow author wrote."""
    if key_raw is None:
        return ""
    op = str(op_raw) if op_raw is not None else "=="
    if expected_raw is None:
        return f"{key_raw} {op}".rstrip()
    return f"{key_raw} {op} {expected_raw}"


class SwitchNodeHandler(RoutingHandlerBase):
    """Multi-way routing.

    Selection has two-stage fallback semantics (port-id match → branch
    label match → default edge), and the default edge only fires when
    no other edge matched. The acceptance set therefore depends on the
    whole edge list, not on each edge in isolation — so we override
    ``handle`` to compute the taken edges in one place rather than via
    per-edge predicate.

    ``select_port`` remains the canonical entry point and is exposed
    for testability; ``handle`` calls it and then resolves
    ``taken_edge_ports`` against the topology before populating the
    ``NodeHandlerResult``.
    """

    METADATA_TYPE = "switch"

    def __init__(self, variable_resolver: VariableResolver) -> None:
        self._resolver = variable_resolver

    async def select_port(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
    ) -> RoutingDecision:
        # Evaluation phase only — the actual edge selection happens in
        # ``handle`` because the default-edge fallback depends on the
        # full edge list, not on each edge in isolation. This method
        # exists for testability (subclasses / harness can call it to
        # inspect the evaluation result before routing).
        decision_state = await self._evaluate(node, run)
        completion_payload = {
            "branch": decision_state["event_branch"],
            "expression": decision_state["evaluated_expression"],
            "condition_result": bool(decision_state["event_branch"]),
        }
        return RoutingDecision(
            matched_branch=decision_state.get("matched_branch"),
            matched_port_id=decision_state.get("matched_port_id", ""),
            completion_payload=completion_payload,
            edge_accepts=self._edge_accepts_stub(decision_state),
            output=self._build_output(decision_state),
        )

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
        result.completion_mode = "immediate"
        result.state_updates[node.id] = {
            "output": {},
            "metadata": {"type": self.METADATA_TYPE},
        }

        decision_state = await self._evaluate(node, run)
        outgoing_edges = topology.edge_map.get(node.id, [])
        taken = self._select_taken_edges(decision_state, outgoing_edges)

        if not taken:
            logger.warning(
                "Switch node %s: no case matched and no default edge found — "
                "node will produce no successors, which may stall the run. "
                "resolved_value=%r, matched_branch=%r",
                node.id,
                decision_state.get("resolved_value"),
                decision_state.get("matched_branch"),
            )

        result.completion_payload = {
            "branch": decision_state["event_branch"],
            "expression": decision_state["evaluated_expression"],
            "condition_result": bool(decision_state["event_branch"]),
        }
        result.state_updates[node.id]["output"] = self._build_output(decision_state)

        matched_branch = decision_state.get("matched_branch")
        for edge in taken:
            result.taken_edge_ports[edge.id] = (
                edge.source_port_id
                or matched_branch
                or edge.condition
                or "default"
            )
        return result

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    @staticmethod
    def _build_output(state: dict[str, Any]) -> dict[str, Any]:
        """Node-output trace: the form-declared fields (evaluated_value /
        matched_case / branch) plus the raw and resolved expression."""
        return {
            "evaluated_value": state.get("resolved_value"),
            "matched_case": state.get("matched_branch") or state.get("matched_target"),
            "branch": state.get("event_branch", ""),
            "expression": state.get("evaluated_expression", ""),
            "resolved": state.get("resolved_expression", ""),
        }

    async def _evaluate(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
    ) -> dict[str, Any]:
        """Single source of truth for the SWITCH selection state.

        Returns a dict with keys: ``matched_branch`` (str|None),
        ``matched_target`` (str|None), ``matched_port_id`` (str),
        ``evaluated_expression`` (str), ``resolved_value`` (Any),
        ``event_branch`` (str — first non-empty of matched_branch /
        matched_target).
        """
        cfg = SwitchNodeConfig.from_node(node)
        structured_data = await self._resolve_structured_data(cfg, run)
        matched_branch: str | None = None
        matched_target: str | None = None
        matched_port_id: str = ""
        evaluated_expression: str = ""
        resolved_expression: str = ""
        resolved_value: Any = None

        if structured_data is not None:
            (
                matched_branch,
                evaluated_expression,
                resolved_value,
                matched_port_id,
                resolved_expression,
            ) = await self._evaluate_structured(
                structured_data, run.context.variables,
            )
        else:
            evaluated_expression = cfg.expression
            try:
                resolved_value = await self._resolver.resolve(
                    evaluated_expression, run.context.variables,
                )
                resolved_expression = str(resolved_value)
            except VariableResolutionError:
                resolved_value = None
                resolved_expression = evaluated_expression
            cases = cfg.cases
            default_target = cfg.default
            for case in cases:
                case_value = case.get("value")
                case_values = case.get("values", [])
                target = case.get("target")
                # SA-1756: type-aware equality. The structured-rule path
                # already routes through ``Comparison`` (int 1 == float 1.0,
                # bool True == "true", numeric strings compared numerically);
                # the legacy ``cases`` path used ``str(x) == str(y)`` which
                # mis-compared those. Route both the single ``value`` and the
                # ``values`` list through the same operator for consistency.
                if case_value is not None and Comparison.evaluate(
                    resolved_value, "==", case_value
                ):
                    matched_target = target
                    break
                if case_values and any(
                    Comparison.evaluate(resolved_value, "==", v) for v in case_values
                ):
                    matched_target = target
                    break
            if matched_target is None:
                matched_target = default_target

        return {
            "matched_branch": matched_branch,
            "matched_target": matched_target,
            "matched_port_id": matched_port_id,
            "evaluated_expression": evaluated_expression,
            "resolved_expression": resolved_expression,
            "resolved_value": resolved_value,
            "event_branch": matched_branch or matched_target or "",
        }

    @staticmethod
    def _select_taken_edges(
        state: dict[str, Any],
        outgoing_edges: list,
    ) -> list:
        """Return the edges to follow given an evaluation state.

        Mirrors the original handler's two-pass logic: collect explicit
        matches first; if nothing matched, fall back to the ``default``
        edge. Structured and legacy modes use different match heuristics.
        """
        matched_branch = state.get("matched_branch")
        matched_target = state.get("matched_target")
        matched_port_id = state.get("matched_port_id", "")
        resolved_value = state.get("resolved_value")
        taken: list = []

        if matched_branch is not None:
            branch_lower = matched_branch.lower()
            for edge in outgoing_edges:
                edge_cond = (edge.condition or "").lower()
                port_id = (edge.source_port_id or "").lower()
                if matched_port_id and edge.source_port_id == matched_port_id:
                    taken.append(edge)
                elif edge_cond == branch_lower or port_id == branch_lower:
                    taken.append(edge)
            if not taken:
                for edge in outgoing_edges:
                    edge_cond = (edge.condition or "").lower()
                    port_id = (edge.source_port_id or "").lower()
                    if edge_cond == "default" or port_id == "default":
                        taken.append(edge)
                        break
        else:
            for edge in outgoing_edges:
                edge_cond = edge.condition or ""
                if matched_target and edge.target_node_id == matched_target:
                    taken.append(edge)
                elif edge_cond == f"case:{resolved_value}":
                    taken.append(edge)
                elif edge_cond == "default" and not taken:
                    taken.append(edge)
            if not taken:
                for edge in outgoing_edges:
                    if (edge.condition or "") == "default":
                        taken.append(edge)
                        break
        return taken

    @staticmethod
    def _edge_accepts_stub(
        state: dict[str, Any],
    ) -> Callable[[Any], Optional[str]]:
        """Stub closure for the :class:`RoutingDecision`.

        SWITCH owns the edge-acceptance logic inside ``handle`` because
        the default fallback depends on the full edge list. The closure
        here is only consulted when ``RoutingHandlerBase.handle`` is
        used directly — but the SWITCH handler overrides ``handle``, so
        this is dead code in practice. It mirrors the per-edge match
        rules for completeness (tests that call ``select_port`` in
        isolation get the same answer for non-default cases).
        """
        matched_branch = state.get("matched_branch")
        matched_target = state.get("matched_target")
        matched_port_id = state.get("matched_port_id", "")
        resolved_value = state.get("resolved_value")

        def accepts(edge: Any) -> Optional[str]:
            edge_cond = (edge.condition or "")
            port_id = (edge.source_port_id or "")
            if matched_branch is not None:
                branch_lower = matched_branch.lower()
                if matched_port_id and edge.source_port_id == matched_port_id:
                    return edge.source_port_id or matched_branch or "default"
                if edge_cond.lower() == branch_lower or port_id.lower() == branch_lower:
                    return edge.source_port_id or matched_branch or "default"
            else:
                if matched_target and edge.target_node_id == matched_target:
                    return edge.source_port_id or edge.condition or "default"
                if edge_cond == f"case:{resolved_value}":
                    return edge.source_port_id or edge.condition or "default"
            return None

        return accepts

    # ------------------------------------------------------------------
    # Structured data support
    # ------------------------------------------------------------------

    async def _resolve_structured_data(
        self,
        cfg: SwitchNodeConfig,
        run: WorkflowRun,
    ) -> list[dict] | None:
        """Return the raw list of switch rules from ``config.data``.

        Two shapes are supported:
          1. ``data`` is a list of rule dicts → returned as-is. Per-rule
             field resolution happens at evaluation time so the unresolved
             ``key`` expression remains available for the NodeCompleted
             event's ``expression`` field.
          2. ``data`` is a string expression that resolves to a list →
             resolve once and return the rule list it produced.
        """
        data = cfg.data
        if data is None or data == () or data == []:
            return None
        if isinstance(data, str):
            try:
                resolved = await self._resolver.resolve(data, run.context.variables)
                if isinstance(resolved, list):
                    return resolved
            except VariableResolutionError:
                return None
            if isinstance(resolved, list):
                rules = [r for r in resolved if isinstance(r, dict)]
                return rules or None
            return None
        if isinstance(data, (list, tuple)):
            rules = [r for r in data if isinstance(r, dict)]
            return rules or None
        return None

    async def _evaluate_structured(
        self,
        rules: list[dict],
        context: dict,
    ) -> tuple[str | None, str, Any, str, str]:
        """Evaluate structured rules top-to-bottom.

        Returns ``(matched_branch, rule_expression, resolved_value, port_id,
        resolved_expression)``:
          * ``matched_branch`` — name from the matched rule, or ``None``.
          * ``rule_expression`` — the matched rule formatted as a
            ``"<key> <op> <value>"`` comparison string using the *unresolved*
            templates (or the first rule's expression when nothing matched,
            for debug context).
          * ``resolved_value`` — the resolved value of that key expression.
          * ``port_id`` — the rule's explicit ``port_id`` (UUID identifying
            the target out-port), or ``""`` if the rule didn't carry one.
          * ``resolved_expression`` — the same comparison string with the
            templates substituted (``"<resolved key> <op> <resolved value>"``),
            for the node's output panel.
        """
        first_rule_repr = ""
        for rule in rules:
            key_raw = rule.get("key")
            op_raw = rule.get("operations", "==")
            expected_raw = rule.get("value")
            branch_raw = rule.get("branch", "")
            port_id_raw = rule.get("port_id", "")

            if not first_rule_repr and key_raw is not None:
                first_rule_repr = _format_rule_expression(key_raw, op_raw, expected_raw)

            try:
                if isinstance(key_raw, str):
                    key_val = await self._resolver.resolve(key_raw, context)
                else:
                    key_val = key_raw
            except VariableResolutionError:
                continue

            op = await self._resolve_or_keep(op_raw, context)
            expected = await self._resolve_or_keep(expected_raw, context)
            branch = await self._resolve_or_keep(branch_raw, context)

            if Comparison.evaluate(key_val, op, expected):
                return (
                    str(branch) if branch else None,
                    _format_rule_expression(key_raw, op_raw, expected_raw),
                    key_val,
                    str(port_id_raw) if port_id_raw else "",
                    _format_rule_expression(key_val, op, expected),
                )
        return (None, first_rule_repr, None, "", "")

    async def _resolve_or_keep(self, value: Any, context: dict) -> Any:
        """Resolve ``{{...}}`` templates in a string; pass non-strings through."""
        if not isinstance(value, str):
            return value
        try:
            return await self._resolver.resolve(value, context)
        except VariableResolutionError:
            return value
