from typing import Any


def _validate_compiled_subgraph(compiled_graph: Any, *, context: str) -> Any:
    if callable(getattr(compiled_graph, "invoke", None)) and callable(
        getattr(compiled_graph, "stream", None)
    ):
        return compiled_graph
    raise TypeError(
        f"{context} must be a compiled subgraph with callable 'invoke' and 'stream' methods"
    )
