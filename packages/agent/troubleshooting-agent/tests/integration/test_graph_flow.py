"""Graph-level regression: compile the real COLLECT -> DIAGNOSE -> REPORT graph and invoke it,
which the direct-usecase e2e test skips. Only a compiled LangGraph enforces the MainState
channels — so this is the test that catches `diagnosis` being dropped (KeyError in report_node)
and the diagnosis never reaching the SDK `formatted_response` output channel."""
import asyncio

from agent_sdk import AgentGraphBuilder

from app.layer2_application.features.troubleshoot import use_cases as uc_pkg
from app.layer4_frameworks.clients.workflow_clients import WorkflowClient
from app.layer4_frameworks.graph.graph_builder import build_graph
from app.layer4_frameworks.repositories.diagnosis_cache import DiagnosisCache


class _Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def debug(self, *a, **k): pass


def _compile_real_graph():
    logger = _Log()
    deps = {
        "logger": logger,
        "workflow_client": WorkflowClient("", use_mock=True),   # offline mock failed run
        "diagnosis_cache": DiagnosisCache(),
    }
    # Fresh use-case wiring with these deps (the container is a module-global cache).
    uc_pkg.USECASE_CONTAINER.clear()
    uc_pkg.FEATURE_REGISTRY.clear()
    uc_pkg.load_features(logger)
    uc_pkg.load_all_usecases(deps, logger)
    return build_graph(deps=deps, checkpointer=None, builder_factory=AgentGraphBuilder)


def test_graph_surfaces_diagnosis_in_formatted_response():
    graph = _compile_real_graph()
    final = asyncio.run(graph.ainvoke({"parameters": {"run_id": "r-graph-1"}}))

    # Regression: `diagnosis` used to be dropped (not a MainState channel) -> report_node KeyError.
    fr = final.get("formatted_response")
    assert fr, "formatted_response missing — diagnosis did not survive the graph"
    assert fr["status"] == "success"
    assert fr["content"]                        # the one-line summary
    assert fr["agent_data"]["category"]         # the structured diagnosis rode through to output
    assert fr["agent_data"]["owner"]            # owner routing present
