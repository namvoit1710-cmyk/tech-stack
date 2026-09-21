import importlib
import sys

from app.layer2_application.enums.graph_node_enum import GraphNode
from app.layer1_domain.entities.state import MainState
from app.layer2_application.graphs.edges.review_outcome import route_after_review


class _FakeCompiledGraph:
    def __init__(self, checkpointer):
        self.checkpointer = checkpointer


class _FakeBuilder:
    def __init__(self, *, deps, state_schema):
        self.deps = deps
        self.state_schema = state_schema
        self.nodes: list[tuple[str, object]] = []
        self.edges: list[tuple[str, str]] = []
        self.conditional_edges: list[tuple[str, object, dict[str, str]]] = []
        self.entry_point = None

    def add_node(self, name: str, fn):
        self.nodes.append((name, fn))

    def set_entry_point(self, name: str):
        self.entry_point = name

    def add_edge(self, source: str, target: str):
        self.edges.append((source, target))

    def add_conditional_edges(self, from_node: str, router_fn, path_map: dict[str, str]):
        self.conditional_edges.append((from_node, router_fn, path_map))

    def compile(self, checkpointer=None):
        return _FakeCompiledGraph(checkpointer)


def _load_graph_modules():
    usecases = importlib.import_module("app.layer2_application.features.main_agent.use_cases")
    usecases.get_usecase = lambda *_args, **_kwargs: object()

    for module_name in (
        "app.layer2_application.graphs.nodes.execute_node",
        "app.layer2_application.graphs.nodes.plan_usecase",
        "app.layer2_application.graphs.nodes.review_node",
        "app.layer2_application.graphs.main_flow",
        "app.layer4_frameworks.graph.graph_builder",
    ):
        sys.modules.pop(module_name, None)

    main_flow = importlib.import_module("app.layer2_application.graphs.main_flow")
    graph_builder = importlib.import_module("app.layer4_frameworks.graph.graph_builder")
    return main_flow.register_main_flow, graph_builder._compile_graph, graph_builder.build_graph


def test_register_main_flow_sets_expected_nodes_and_edges():
    register_main_flow, _, _ = _load_graph_modules()
    builder = _FakeBuilder(deps={}, state_schema=dict)

    result = register_main_flow(builder)

    assert result is builder
    assert [name for name, _ in builder.nodes] == [
        GraphNode.PLAN.value,
        GraphNode.EXECUTE.value,
        GraphNode.REVIEW.value,
    ]
    assert builder.entry_point == GraphNode.PLAN.value
    assert builder.edges == [
        (
            GraphNode.PLAN.value,
            GraphNode.EXECUTE.value,
        ),
        (
            GraphNode.EXECUTE.value,
            GraphNode.REVIEW.value,
        )
    ]
    assert builder.conditional_edges[0][0] == GraphNode.REVIEW.value
    assert builder.conditional_edges[0][1] is route_after_review
    assert builder.conditional_edges[0][2] == {
        GraphNode.PLAN.value: GraphNode.PLAN.value,
        "__end__": "__end__",
    }


def test_route_after_review_ends_when_state_is_reviewed():
    assert route_after_review({"is_reviewed": True}) == "__end__"


def test_route_after_review_ends_by_default_and_loops_when_explicitly_not_reviewed():
    assert route_after_review({}) == "__end__"
    assert route_after_review({"is_reviewed": False}) == GraphNode.PLAN.value


def test_build_graph_requires_builder_factory():
    _, _, build_graph = _load_graph_modules()
    try:
        build_graph(deps={})
    except ValueError as exc:
        assert "builder_factory is required" in str(exc)
    else:
        raise AssertionError("build_graph should require builder_factory")


def test_compile_graph_uses_main_state_and_checkpointer():
    register_main_flow, _compile_graph, _ = _load_graph_modules()
    checkpointer = object()

    compiled = _compile_graph(
        register_main_flow,
        deps={"sample": "dependency"},
        checkpointer=checkpointer,
        builder_factory=_FakeBuilder,
    )

    assert isinstance(compiled, _FakeCompiledGraph)
    assert compiled.checkpointer is checkpointer
    compiled_builder = _FakeBuilder(deps={"sample": "dependency"}, state_schema=MainState)
    assert compiled_builder.state_schema is MainState


def test_build_graph_aliases_main_graph_builder():
    _, _, build_graph = _load_graph_modules()
    compiled = build_graph(
        deps={"sample": "dependency"},
        checkpointer=None,
        builder_factory=_FakeBuilder,
    )

    assert isinstance(compiled, _FakeCompiledGraph)

