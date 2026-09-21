from __future__ import annotations

from typing import Any

from app.layer1_domain.interfaces.workflow_control_plane_interface import (
    IWorkflowControlPlane,
)
from app.layer2_application.interfaces.catalog_provider_interface import ICatalogProvider
from app.layer2_application.interfaces.workflow_diagnoser_interface import (
    IWorkflowDiagnoser,
)
from app.layer2_application.interfaces.workflow_run_reader_interface import (
    IWorkflowRunReader,
)
from app.layer4_frameworks.tools.ask_for_clarify import ask_for_clarify
from app.layer4_frameworks.tools.authoring_tools import build_authoring_tools
from app.layer4_frameworks.tools.catalog_tools import build_catalog_tools
from app.layer4_frameworks.tools.diagnosis_tools import build_diagnosis_tools
from app.layer4_frameworks.tools.plan_workflow import plan_workflow


def build_local_tools(
    catalog: ICatalogProvider,
    control_plane: IWorkflowControlPlane,
    reader: IWorkflowRunReader | None = None,
    diagnoser: IWorkflowDiagnoser | None = None,
) -> list[Any]:
    """The tools the agent's loop can call: discover the palette (list_node_types),
    read a node's config schema (get_node_schema), record a finalized node plan
    (plan_workflow), dry-validate (validate_workflow), persist + test-run
    (create_workflow), ask the user (ask_for_clarify), or — when a reader + diagnoser are
    wired — diagnose a failed run (diagnose_failure) as the first step of self-heal."""
    tools = [
        *build_catalog_tools(catalog),
        plan_workflow,
        *build_authoring_tools(control_plane),
        ask_for_clarify,
    ]
    if reader is not None and diagnoser is not None:
        tools.extend(build_diagnosis_tools(reader, diagnoser))
    return tools


def tool_catalog(tools: list[Any]) -> list[dict[str, str]]:
    """Name + description of each tool, for a tool-selection prompt / logging."""
    return [{"name": t.name, "description": getattr(t, "description", "")} for t in tools]
