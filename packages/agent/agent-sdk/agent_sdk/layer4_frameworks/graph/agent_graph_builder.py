from __future__ import annotations

import inspect
from collections.abc import Hashable
from typing import Any, Callable, cast

from langchain_core.tools import BaseTool
from langgraph.graph import END
from langgraph.prebuilt import ToolNode, tools_condition

from agent_sdk.layer1_domain.exceptions import NodeExecutionError
from agent_sdk.layer2_application.utils.dependency_resolver import (
    ensure_dependency_resolver,
)
from agent_sdk.layer2_application.utils.state_resolver import ensure_state_resolver
from agent_sdk.layer4_frameworks.graph.primitives import _validate_compiled_subgraph


def _is_graph_interrupt_exception(exc: BaseException) -> bool:
    """True when ``exc`` is LangGraph runtime HITL interrupt control flow.

    AgentGraphBuilder wraps normal node failures into NodeExecutionError, but
    LangGraph interrupt() raises GraphInterrupt as normal control flow.  If we
    wrap it, ExecuteAgentUseCase sees AGENT_GRAPH_ERROR instead of returning
    status="interrupted".
    """
    if type(exc).__name__ == "GraphInterrupt":
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_is_graph_interrupt_exception(child) for child in exc.exceptions)
    return False


class AgentGraphBuilder:
    def __init__(
        self, deps: dict[str, Any] | None = None, state_schema: type | None = None
    ) -> None:
        self._deps: dict[str, Any] = deps or {}
        self._state_schema = state_schema
        self._nodes: list[tuple[str, Any]] = []
        self._entry_point: str | None = None
        self._edges: list[tuple[str, str]] = []
        self._conditional_edges: list[tuple[str, Callable, dict[Hashable, str]]] = []
        self._tools: list[BaseTool] = []

    def add_node(self, name: str, fn: Any) -> "AgentGraphBuilder":
        self._nodes.append((name, fn))
        return self

    def add_node_if(
        self, condition: bool, name: str, fn: Callable
    ) -> "AgentGraphBuilder":
        if condition:
            self.add_node(name, fn)
        return self

    def set_entry_point(self, name: str) -> "AgentGraphBuilder":
        self._entry_point = name
        return self

    def add_edge(self, from_node: str, to_node: str) -> "AgentGraphBuilder":
        self._edges.append((from_node, to_node))
        return self

    def add_conditional_edges(
        self, from_node: str, router_fn: Callable, path_map: dict[Hashable, str]
    ) -> "AgentGraphBuilder":
        self._conditional_edges.append((from_node, router_fn, path_map))
        return self

    def add_tools(self, tools: list[BaseTool]) -> "AgentGraphBuilder":
        self._tools = tools
        return self

    def add_tool_node(self, name: str = "tools") -> "AgentGraphBuilder":
        self.add_node(name, ToolNode(self._tools))
        return self

    def add_tools_condition(
        self, from_node: str, tools_node: str = "tools", next_node: str = END
    ) -> "AgentGraphBuilder":
        self.add_conditional_edges(
            from_node, tools_condition, {"tools": tools_node, END: next_node}
        )
        return self

    def add_subgraph(self, name: str, compiled_graph: Any) -> "AgentGraphBuilder":
        _validate_compiled_subgraph(compiled_graph, context=f"Subgraph '{name}'")
        self.add_node(name, compiled_graph)
        return self

    def add_mapped_subgraph(
        self,
        name: str,
        compiled_graph: Any,
        map_input: Callable[[Any], dict],
        map_output: Callable[[dict, Any], dict],
    ) -> "AgentGraphBuilder":
        _validate_compiled_subgraph(compiled_graph, context=f"Subgraph '{name}'")

        async def node(state):
            subgraph_input = map_input(state)
            subgraph_output = await compiled_graph.ainvoke(subgraph_input)
            return map_output(subgraph_output, state)

        node.__name__ = name
        self.add_node(name, node)
        return self

    def compile(
        self,
        checkpointer: Any | None = None,
        interrupt_before: list[str] | str | None = None,
        interrupt_after: list[str] | str | None = None,
        **compile_kwargs: Any,
    ) -> Any:
        from langgraph.graph import StateGraph

        if not self._nodes:
            raise ValueError("AgentGraphBuilder: no nodes have been added.")
        if self._entry_point is None:
            raise ValueError(
                "AgentGraphBuilder: entry point must be set before compile()."
            )
        state_schema = self._state_schema if self._state_schema is not None else dict
        graph = StateGraph(cast(Any, state_schema))
        deps = self._deps

        def _serialize_error_context(exc: Exception) -> dict[str, Any]:
            metadata = getattr(exc, "metadata", None)
            if not isinstance(metadata, dict):
                metadata = None
            return {
                "exception_type": type(exc).__name__,
                "message": str(exc),
                "error_code": getattr(exc, "error_code", None),
                "related_step_id": getattr(exc, "related_step_id", None),
                "critical": bool(
                    getattr(exc, "is_critical", getattr(exc, "critical", False))
                ),
                "metadata": metadata,
            }

        def _build_injected_args(
            fn: Callable,
            state: Any,
            params: Any,
            runtime_config: Any | None = None,
        ) -> list[Any]:
            state_resolver = None
            dependency_resolver = None
            injected_args: list[Any] = []

            for index, (name, _) in enumerate(params.items()):
                if index == 0:
                    injected_args.append(state)
                elif name == "config":
                    injected_args.append(runtime_config)
                elif name == "deps":
                    injected_args.append(deps)
                elif name in {"dependency_resolver", "deps_resolver"}:
                    if dependency_resolver is None:
                        dependency_resolver = ensure_dependency_resolver(deps)
                    injected_args.append(dependency_resolver)
                elif name == "state_resolver":
                    if state_resolver is None:
                        state_resolver = ensure_state_resolver(state)
                    injected_args.append(state_resolver)
                else:
                    # Backward-compatible default: older custom nodes often used
                    # a second positional dependency argument with an arbitrary
                    # name. Keep injecting deps for those nodes instead of
                    # treating every second argument as LangGraph config.
                    injected_args.append(deps)

            return injected_args

        def _node(fn: Callable) -> Callable:
            if hasattr(fn, "invoke") and hasattr(fn, "stream"):
                return fn
            try:
                params = inspect.signature(fn).parameters
            except (ValueError, TypeError):
                return fn
            if len(params) == 1:
                return fn
            if inspect.iscoroutinefunction(fn):

                async def wrapped_async(state, config=None):
                    try:
                        return await fn(
                            *_build_injected_args(fn, state, params, config)
                        )
                    except NodeExecutionError:
                        raise
                    except Exception as exc:
                        if _is_graph_interrupt_exception(exc):
                            raise
                        raise NodeExecutionError(
                            str(exc), error_context=_serialize_error_context(exc)
                        ) from exc

                wrapped_async.__name__ = getattr(fn, "__name__", "node")
                return wrapped_async

            def wrapped_sync(state, config=None):
                try:
                    return fn(*_build_injected_args(fn, state, params, config))
                except NodeExecutionError:
                    raise
                except Exception as exc:
                    if _is_graph_interrupt_exception(exc):
                        raise
                    raise NodeExecutionError(
                        str(exc), error_context=_serialize_error_context(exc)
                    ) from exc

            wrapped_sync.__name__ = getattr(fn, "__name__", "node")
            return wrapped_sync

        for name, fn in self._nodes:
            graph.add_node(name, _node(fn))
        graph.set_entry_point(self._entry_point)
        for from_node, to_node in self._edges:
            graph.add_edge(from_node, to_node)
        for from_node, router_fn, path_map in self._conditional_edges:
            graph.add_conditional_edges(from_node, router_fn, path_map)
        resolved_compile_kwargs = dict(compile_kwargs)
        resolved_compile_kwargs.setdefault("checkpointer", checkpointer)
        if interrupt_before is not None:
            resolved_compile_kwargs["interrupt_before"] = interrupt_before
        if interrupt_after is not None:
            resolved_compile_kwargs["interrupt_after"] = interrupt_after
        return graph.compile(**resolved_compile_kwargs)
