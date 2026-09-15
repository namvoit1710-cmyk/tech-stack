"""NODE_CONFIG_VALIDATION wiring in the create/update workflow use cases.

Modes: off (skip) / warn (log, never block — default) / enforce
(error-severity violations → NODE_CONFIG_INVALID with violations[]).
"""

import pytest

from app.layer2_application.errors import ErrorCode
from app.layer2_application.features.create_workflow.use_cases.create_workflow_use_case import (
    CreateWorkflowCommand,
    CreateWorkflowUseCase,
)
from app.layer2_application.features.update_workflow.use_cases.update_workflow_use_case import (
    ReplaceWorkflowCommand,
    UpdateWorkflowUseCase,
)
from app.layer2_application.definition.node_config_validator import MODE_ENV_VAR

_BAD_MERGE_NODE = {
    "id": "m1", "name": "Merge", "node_type": "MERGE",
    "parameters": {"merge_strategy": "wait_some"},  # invalid enum
}

_GOOD_TRIGGER_NODE = {
    "id": "t1", "name": "Start", "node_type": "TRIGGER",
    "parameters": {"trigger_type": "manual"},
}


def _create_uc(logger, workflow_repo) -> CreateWorkflowUseCase:
    return CreateWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)


@pytest.mark.asyncio
async def test_enforce_blocks_with_violation_payload(
    logger, workflow_repo, monkeypatch,
) -> None:
    monkeypatch.setenv(MODE_ENV_VAR, "enforce")
    result = await _create_uc(logger, workflow_repo).execute(
        CreateWorkflowCommand(name="wf", nodes=[_BAD_MERGE_NODE]),
    )
    assert result.error_code == ErrorCode.NODE_CONFIG_INVALID.code
    violations = result.error_details["violations"]
    assert violations and violations[0]["node_id"] == "m1"
    assert violations[0]["key"] == "merge_strategy"
    assert violations[0]["code"] == "invalid_enum"


@pytest.mark.asyncio
async def test_enforce_collects_across_all_nodes(
    logger, workflow_repo, monkeypatch,
) -> None:
    monkeypatch.setenv(MODE_ENV_VAR, "enforce")
    bad_approval = {
        "id": "a1", "name": "Approval", "node_type": "APPROVAL_FLOW",
        "parameters": {},
    }
    result = await _create_uc(logger, workflow_repo).execute(
        CreateWorkflowCommand(name="wf", nodes=[_BAD_MERGE_NODE, bad_approval]),
    )
    assert result.error_code == ErrorCode.NODE_CONFIG_INVALID.code
    node_ids = {v["node_id"] for v in result.error_details["violations"]}
    assert node_ids == {"m1", "a1"}


@pytest.mark.asyncio
async def test_warn_mode_logs_but_saves(
    logger, workflow_repo, monkeypatch,
) -> None:
    monkeypatch.setenv(MODE_ENV_VAR, "warn")
    result = await _create_uc(logger, workflow_repo).execute(
        CreateWorkflowCommand(name="wf", nodes=[_BAD_MERGE_NODE]),
    )
    assert result.error == ""
    assert result.id


@pytest.mark.asyncio
async def test_off_mode_skips_validation(
    logger, workflow_repo, monkeypatch,
) -> None:
    monkeypatch.setenv(MODE_ENV_VAR, "off")
    result = await _create_uc(logger, workflow_repo).execute(
        CreateWorkflowCommand(name="wf", nodes=[_BAD_MERGE_NODE]),
    )
    assert result.error == ""


@pytest.mark.asyncio
async def test_enforce_passes_clean_workflow(
    logger, workflow_repo, monkeypatch,
) -> None:
    monkeypatch.setenv(MODE_ENV_VAR, "enforce")
    result = await _create_uc(logger, workflow_repo).execute(
        CreateWorkflowCommand(name="wf", nodes=[_GOOD_TRIGGER_NODE]),
    )
    assert result.error == ""
    assert result.id


@pytest.mark.asyncio
async def test_update_replace_enforces_too(
    logger, workflow_repo, monkeypatch,
) -> None:
    monkeypatch.setenv(MODE_ENV_VAR, "enforce")
    created = await _create_uc(logger, workflow_repo).execute(
        CreateWorkflowCommand(name="wf", nodes=[_GOOD_TRIGGER_NODE]),
    )
    assert created.error == ""

    uc = UpdateWorkflowUseCase(logger=logger, workflow_repo=workflow_repo)
    result = await uc.execute(ReplaceWorkflowCommand(
        workflow_id=created.id, name="wf",
        nodes=[_BAD_MERGE_NODE], edges=[],
    ))
    assert result.error_code == ErrorCode.NODE_CONFIG_INVALID.code
