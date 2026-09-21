from __future__ import annotations

import inspect
import logging
from collections.abc import Hashable
from typing import Any, cast

from agent_sdk.layer1_domain.entities.flow_config import FlowConfig, StepConfig
from agent_sdk.layer2_application.services.node_type_registry import NodeTypeRegistry
from agent_sdk.layer4_frameworks.graph.primitives import (
    _validate_compiled_subgraph,
)

logger = logging.getLogger(__name__)


class FlowGraphBuilder:
    def __init__(
        self,
        registry: NodeTypeRegistry,
        deps: dict[str, Any] | None = None,
        checkpointer: Any = None,
        log: logging.Logger | None = None,
    ) -> None:
        self._registry = registry
        self._deps: dict[str, Any] = deps or {}
        self._checkpointer = checkpointer
        self._logger = log or logger

    def build(
        self,
        flow_config: FlowConfig,
        interrupt_before: list[str] | str | None = None,
        interrupt_after: list[str] | str | None = None,
        subgraphs: dict[str, Any] | None = None,
    ) -> Any:
        from langgraph.graph import END, StateGraph

        steps = flow_config.steps
        if not steps:
            raise ValueError(
                f"Empty flow config: {flow_config.agent_type}.{flow_config.flow_type} has no steps."
            )
        for step in steps:
            if step.type == "subgraph":
                continue
            if not self._registry.has(step.type):
                raise ValueError(
                    f"Unknown step type '{step.type}' for step '{step.id}' in {flow_config.agent_type}.{flow_config.flow_type}. Registered types: {self._registry.list_types()}"
                )
        graph = StateGraph(cast(Any, dict))
        valid_step_ids: list[str] = []
        for step in steps:
            if step.type == "subgraph":
                if not subgraphs or step.subflow_ref not in subgraphs:
                    raise ValueError(
                        f"Subgraph '{step.subflow_ref}' not provided for step '{step.id}'"
                    )
                _validate_compiled_subgraph(
                    subgraphs[step.subflow_ref],
                    context=f"Subgraph '{step.subflow_ref}'",
                )
                graph.add_node(step.id, subgraphs[step.subflow_ref])
                valid_step_ids.append(step.id)
            else:
                node_fn = self._registry.get(step.type)
                wrapped = self._wrap_step(node_fn, step, self._deps)
                graph.add_node(step.id, wrapped)
                valid_step_ids.append(step.id)
        graph.set_entry_point(valid_step_ids[0])
        id_set = set(valid_step_ids)
        for i, step in enumerate(steps):
            step_type = step.type.lower()
            if step_type == "condition":
                edge_map: dict[str, str] = {}
                if step.true_branch and step.true_branch in id_set:
                    edge_map["true"] = step.true_branch
                if step.false_branch and step.false_branch in id_set:
                    edge_map["false"] = step.false_branch
                if edge_map:
                    sid = step.id

                    def _check(state, _sid=sid):
                        result = state.get(_sid, {})
                        return "true" if result.get("result") else "false"

                    graph.add_conditional_edges(
                        step.id,
                        _check,
                        cast(dict[Hashable, str], edge_map),
                    )
                else:
                    self._add_sequential_edge(graph, step, i, valid_step_ids, END)
            elif step_type == "router":
                routes = step.routes
                if routes:
                    valid_routes = {k: v for k, v in routes.items() if v in id_set}
                    if valid_routes:
                        sid = step.id

                        def _route(state, _sid=sid, _routes=valid_routes):
                            result = state.get(_sid, {})
                            branch = result.get("branch", "default")
                            return _routes.get(branch, _routes.get("default", END))

                        graph.add_conditional_edges(
                            step.id,
                            _route,
                            cast(dict[Hashable, str], {**valid_routes, END: END}),
                        )
                    else:
                        self._add_sequential_edge(graph, step, i, valid_step_ids, END)
                else:
                    self._add_sequential_edge(graph, step, i, valid_step_ids, END)
            else:
                self._add_sequential_edge(graph, step, i, valid_step_ids, END)
        compile_kwargs = {"checkpointer": self._checkpointer}
        if interrupt_before is not None:
            compile_kwargs["interrupt_before"] = interrupt_before
        if interrupt_after is not None:
            compile_kwargs["interrupt_after"] = interrupt_after
        compiled = graph.compile(**compile_kwargs)
        self._logger.info(
            "Flow graph compiled: %s.%s (%d steps)",
            flow_config.agent_type,
            flow_config.flow_type,
            len(valid_step_ids),
        )
        return compiled

    @staticmethod
    def _add_sequential_edge(
        graph, step: StepConfig, index: int, step_ids: list[str], END
    ) -> None:
        id_set = set(step_ids)
        if step.next_step and step.next_step in id_set:
            graph.add_edge(step.id, step.next_step)
        elif index + 1 < len(step_ids):
            graph.add_edge(step.id, step_ids[index + 1])
        else:
            graph.add_edge(step.id, END)

    @staticmethod
    def _wrap_step(node_fn, step_config: StepConfig, deps: dict):
        step_id = step_config.id
        if inspect.iscoroutinefunction(node_fn):

            async def wrapped_async(state):
                return await node_fn(state, step_config, deps)

            wrapped_async.__name__ = f"step_{step_id}"
            return wrapped_async

        def wrapped_sync(state):
            return node_fn(state, step_config, deps)

        wrapped_sync.__name__ = f"step_{step_id}"
        return wrapped_sync
