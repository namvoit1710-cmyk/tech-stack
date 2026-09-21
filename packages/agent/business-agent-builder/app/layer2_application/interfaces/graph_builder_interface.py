from typing import Any, Callable, Protocol


class IGraphBuilder(Protocol):
    def add_node(self, name: str, fn: Callable) -> Any:
        ...

    def add_node_if(
        self, condition: bool, name: str, fn: Callable
    ) -> Any:
        ...

    def set_entry_point(self, name: str) -> Any:
        ...

    def add_edge(self, from_node: str, to_node: str) -> Any:
        ...

    def add_conditional_edges(
        self, from_node: str, router_fn: Callable, path_map: dict[str, str]
    ) -> Any:
        ...

    def add_tools(self, tools: list[Any]) -> Any:
        ...

    def add_tool_node(self, name: str = "tools") -> Any:
        ...

    def add_tools_condition(
        self, from_node: str, tools_node: str = "tools", next_node: str = "end"
    ) -> Any:
        ...

    def add_subgraph(self, name: str, compiled_graph: Any) -> Any:
        ...

    def add_mapped_subgraph(
        self,
        name: str,
        compiled_graph: Any,
        map_input: Callable[[Any], dict],
        map_output: Callable[[dict, Any], dict],
    ) -> Any:
        ...

    def compile(
        self,
        checkpointer: Any | None = None,
        interrupt_before: list[str] | str | None = None,
        interrupt_after: list[str] | str | None = None,
    ) -> Any:
        ...