from typing import Any, Optional

from app.layer1_domain.entities.state import MainState
from app.layer2_application.graphs.main_flow import register_main_flow


def _compile_graph(
    register_graph,
    *,
    deps: Optional[dict] = None,
    checkpointer=None,
    builder_factory: Any | None = None,
):
    deps = deps or {}
    if builder_factory is None:
        raise ValueError("builder_factory is required to build the agent graph")

    builder = builder_factory(deps=deps, state_schema=MainState
)
    register_graph(builder)
    return builder.compile(checkpointer=checkpointer)


def build_graph(
    deps: Optional[dict] = None,
    checkpointer=None,
    builder_factory: Any | None = None,
):
    return _compile_graph(
        register_main_flow,
        deps=deps,
        checkpointer=checkpointer,
        builder_factory=builder_factory,
    )
