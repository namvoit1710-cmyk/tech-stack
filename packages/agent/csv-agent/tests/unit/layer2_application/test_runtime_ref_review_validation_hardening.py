from __future__ import annotations

import pytest

from app.layer1_domain.exceptions import InvalidInputError
from app.layer2_application.graphs.edges import review_outcome
from app.layer2_application.graphs.edges.review_outcome import make_route_after_review
from app.layer2_application.graphs.nodes.execute_node import execute_node
from app.layer2_application.graphs.nodes.review_node import review_node
from app.layer2_application.interfaces import llm_service
from app.layer4_frameworks.dependency.ioc_container import (
    _probe_runtime_dependency_refs,
)
from app.layer4_frameworks.dependency.runtime_dependency_refs import (
    RuntimeDependencyRef,
)


class _ReviewUseCase:
    async def execute(self, input_dto):
        class _Output:
            is_reviewed = True
            feedback = "ok"
            success = True

        return _Output()


def test_runtime_dependency_probe_fails_fast_for_unwired_ref():
    deps = {
        "llm_service_ref": RuntimeDependencyRef("llm_service"),
        "structured_llm_gateway_ref": RuntimeDependencyRef("structured_llm_gateway"),
    }
    with pytest.raises(RuntimeError, match="llm_service dependency is not configured"):
        _probe_runtime_dependency_refs(deps)


def test_runtime_dependency_probe_passes_after_refs_are_wired():
    deps = {
        "llm_service_ref": RuntimeDependencyRef("llm_service"),
        "structured_llm_gateway_ref": RuntimeDependencyRef("structured_llm_gateway"),
        "data_factory_gateway_ref": RuntimeDependencyRef("data_factory_gateway"),
    }
    deps["llm_service_ref"].set(object())
    deps["structured_llm_gateway_ref"].set(object())
    deps["data_factory_gateway_ref"].set(object())
    _probe_runtime_dependency_refs(deps)


def test_review_router_uses_injected_default_limit():
    route = make_route_after_review(default_max_review_iterations=5)
    assert route({"is_reviewed": False, "review_iteration": 3}) == "plan"
    assert route({"is_reviewed": False, "review_iteration": 5}) == "__end__"


def test_review_outcome_has_no_default_legacy_router():
    assert not hasattr(review_outcome, "route_after_review")


@pytest.mark.asyncio
async def test_execute_node_calls_domain_validation_for_empty_plan():
    with pytest.raises(InvalidInputError, match="Plan must contain at least one step"):
        await execute_node(
            {"execution_plan": {"goal": "x", "steps": []}}, {"worker_tools": []}
        )


@pytest.mark.asyncio
async def test_review_node_calls_execution_result_validation():
    with pytest.raises(
        InvalidInputError, match="Must contain at least one step result"
    ):
        await review_node(
            {"execution_result": {"success": True, "step_results": []}},
            {"review_usecase": _ReviewUseCase()},
        )


def test_llm_service_interface_is_clean_reexport_only():
    assert hasattr(llm_service, "ILLMService")
    assert not hasattr(llm_service, "configure_llm_service")
    assert not hasattr(llm_service, "get_llm_service")
    assert llm_service.__all__ == ["ILLMService"]
