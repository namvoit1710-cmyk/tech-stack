from types import SimpleNamespace

import pytest

from agent_sdk.layer1_domain.entities.agent_shared_state import Metadata, Task
from app.layer2_application.features.main_agent.use_cases.create_plan_usecase import (
    CreatePlanUseCase,
    PLAN_CASES,
    UnsupportedEventTypeError,
)


class DummyLogger:
    def info(self, message: str, **kwargs) -> None:
        pass

    def error(self, message: str, **kwargs) -> None:
        pass

    def warning(self, message: str, **kwargs) -> None:
        pass

    def debug(self, message: str, **kwargs) -> None:
        pass


# def _usecase(shared_state_repository=None) -> CreatePlanUseCase:
#     repository = shared_state_repository or SimpleNamespace(get_metadata=lambda conv_id: None)
#     return CreatePlanUseCase(
#         planner_resolver=SimpleNamespace(),
#         agent_hub_client=SimpleNamespace(),
#         agent_shared_state_repository=repository,
#         workflow_client=SimpleNamespace(),
#         push_gateway_notifier=SimpleNamespace(),
#         logger=DummyLogger(),
#         publisher=SimpleNamespace(),
#         uuid_builder=SimpleNamespace(create_uuid7=lambda: "uuid-1"),
#     )


# def _task(**updates) -> Task:
#     payload = {
#         "task_id": "task-1",
#         "agent": "agent.agent-1",
#         "name": "Plan task",
#     }
#     payload.update(updates)
#     return Task(**payload)


# @pytest.mark.parametrize(
#     ("event_type", "expected_name"),
#     [
#         (event_type, plan_case.name)
#         for plan_case in PLAN_CASES
#         for event_type in plan_case.event_types
#     ],
# )
# def test_resolve_plan_case_maps_known_event_types(event_type, expected_name):
#     subject = _usecase()

#     plan_case = subject._resolve_plan_case(event_type)

#     assert plan_case.name == expected_name


# def test_resolve_plan_case_rejects_unknown_event_type():
#     subject = _usecase()

#     with pytest.raises(UnsupportedEventTypeError, match="Unsupported event type"):
#         subject._resolve_plan_case("unknown")


# def test_get_user_intent_from_task_returns_goal():
#     subject = _usecase()

#     assert subject._get_user_intent_from_task(_task(name="Need help")) == "Need help"


# def test_get_user_intent_from_task_requires_goal():
#     subject = _usecase()
#     task = _task().model_copy(update={"goal": None})

#     with pytest.raises(ValueError, match="Task goal is required"):
#         subject._get_user_intent_from_task(task)


# def test_get_agent_id_from_task_extracts_suffix():
#     subject = _usecase()

#     assert subject._get_agent_id_from_task(_task(agent="agent.agent-99")) == "agent-99"


# def test_get_agent_id_from_task_requires_execute_by():
#     subject = _usecase()
#     task = _task().model_copy(update={"execute_by": None})

#     with pytest.raises(ValueError, match="execute_by is None"):
#         subject._get_agent_id_from_task(task)


# def test_get_metadata_returns_repository_value():
#     metadata = Metadata(conversation_id="conv-1", checkpoint_id="cp-1")
#     subject = _usecase(
#         shared_state_repository=SimpleNamespace(get_metadata=lambda conv_id: metadata)
#     )

#     assert subject._get_metadata("conv-1") is metadata


# def test_get_metadata_requires_existing_metadata():
#     subject = _usecase(
#         shared_state_repository=SimpleNamespace(get_metadata=lambda conv_id: None)
#     )

#     with pytest.raises(ValueError, match="No metadata found for conversation conv-1"):
#         subject._get_metadata("conv-1")

def test_inject_workflow_context_adds_missing_run_and_node_ids():
    # SA-890: the sub-chat planner emits only {"data": {...}} for the patch tool,
    # omitting run_id/node_id entirely. They must be injected from the run context,
    # otherwise the tool rejects the call ("run_id Field required"). The caller only invokes
    # this for an allow-listed workflow-node tool, so no schema re-check is done here.
    out = CreatePlanUseCase._inject_workflow_context(
        {"data": {"triggerId": "X"}}, run_id="run-9", node_id="node-9"
    )
    assert out == {"data": {"triggerId": "X"}, "run_id": "run-9", "node_id": "node-9"}


def test_inject_workflow_context_backfills_empty_ids():
    out = CreatePlanUseCase._inject_workflow_context(
        {"run_id": "", "node_id": "", "data": {"k": "v"}}, run_id="r", node_id="n"
    )
    assert out["run_id"] == "r"
    assert out["node_id"] == "n"


def test_inject_workflow_context_preserves_existing_ids():
    # An id the planner already filled is never overwritten; only the empty one is backfilled.
    out = CreatePlanUseCase._inject_workflow_context(
        {"run_id": "keep", "data": {}}, run_id="r", node_id="n"
    )
    assert out["run_id"] == "keep"
    assert out["node_id"] == "n"


def test_registry_short_name_extracts_tool_segment():
    # Registry names are "agent__{service}__{tool}__{version}"; the allow-list matches the
    # tool segment.
    assert (
        CreatePlanUseCase._registry_short_name(
            "agent__executor-runtime__workflow_patch_data__0.1.5"
        )
        == "workflow_patch_data"
    )
    assert (
        CreatePlanUseCase._registry_short_name("workflow_patch_data")
        == "workflow_patch_data"
    )


def test_filter_patch_data_keys_drops_hedged_synonym_key():
    # SA-890: planner hedged "set name to X" as {"name": X, "template_name": X}; only the
    # real schema key survives so the patch + its confirmation reference one field.
    out = CreatePlanUseCase._filter_patch_data_keys(
        {"run_id": "r", "node_id": "n", "data": {"name": "Acme", "template_name": "Acme"}},
        {"template_name", "description", "rules"},
    )
    assert out["data"] == {"template_name": "Acme"}
    assert out["run_id"] == "r" and out["node_id"] == "n"


def test_filter_patch_data_keys_noop_when_all_keys_valid():
    src = {"data": {"template_name": "Acme", "description": "d"}}
    out = CreatePlanUseCase._filter_patch_data_keys(
        src, {"template_name", "description", "rules"}
    )
    assert out == src


def test_filter_patch_data_keys_noop_when_schema_unknown():
    # Empty schema keys → don't drop anything (avoid nuking a valid patch).
    src = {"data": {"template_name": "Acme"}}
    assert CreatePlanUseCase._filter_patch_data_keys(src, set()) == src
    assert CreatePlanUseCase._filter_patch_data_keys(src, None) == src


def test_filter_patch_data_keys_handles_missing_data():
    src = {"run_id": "r", "node_id": "n"}
    assert CreatePlanUseCase._filter_patch_data_keys(src, {"template_name"}) == src
