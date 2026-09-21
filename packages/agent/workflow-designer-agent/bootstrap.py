from agent_sdk import (
    AgentGraphBuilder,
    OpenAIService,
    build_app_container as _sdk_build_app_container,
    create_checkpointer,
    format_response_node,
)

from app.layer1_domain.entities.repair_policy import RepairPolicy
from app.layer1_domain.entities.workflow_design_state import WorkflowDesignState
from app.layer2_application.features.workflow_design.use_cases.design_workflow_use_case import (
    DesignWorkflowUseCase,
)
from app.layer2_application.features.workflow_design.use_cases.validate_repair_gate import (
    ValidateRepairGate,
)
from app.layer4_frameworks.clients.http_catalog_provider import HttpCatalogProvider
from app.layer4_frameworks.clients.troubleshooting_diagnoser_client import (
    RemoteWorkflowDiagnoser,
)
from app.layer4_frameworks.clients.workflow_control_plane_client import (
    HttpWorkflowControlPlane,
)
from app.layer4_frameworks.clients.workflow_run_reader_client import (
    HttpWorkflowRunReader,
)
from app.layer4_frameworks.config.settings_adapter import AppSettingsAdapter
from app.layer4_frameworks.designer.llm_workflow_repairer import LlmWorkflowRepairer
from app.layer4_frameworks.designer.tool_loop_designer import ToolLoopWorkflowDesigner
from app.layer4_frameworks.graph import build_graph
from app.layer4_frameworks.tools.tool_registry import build_local_tools

# Set during build_app_container so the SSE controller can reuse the same flow.
_design_workflow_use_case: DesignWorkflowUseCase | None = None


def get_design_workflow_use_case() -> DesignWorkflowUseCase | None:
    return _design_workflow_use_case


def build_app_container() -> dict:
    global _design_workflow_use_case
    app_settings = AppSettingsAdapter()
    checkpointer = create_checkpointer(app_settings)
    open_ai_service = OpenAIService()

    # Canonical var is WORKFLOW_API_BASE_URL; fall back to the WORKFLOW_API_URL alias.
    base_url = app_settings.WORKFLOW_API_BASE_URL
    if not base_url.startswith(("http://", "https://")):
        base_url = app_settings.WORKFLOW_API_URL or base_url

    # The live catalog is the single source of truth for node types, config schemas and
    # ports — no node metadata is hardcoded in the agent.
    catalog = HttpCatalogProvider(
        control_plane_base=base_url,
        registry_base=getattr(app_settings, "REGISTRY_URL", "") or "",
    )
    control_plane = HttpWorkflowControlPlane(
        base_url=base_url,
        app_url=app_settings.WORKFLOW_APP_URL,
        catalog=catalog,
    )

    # Self-heal reads a failed run from the same control plane and diagnoses it via the
    # troubleshooting-agent (SA-1438) over HTTP — Solution A, isolated behind the diagnoser
    # adapter so the transport is easy to refactor. When no URL is configured the diagnoser
    # degrades to "no failure" and the diagnose_failure tool simply reports nothing to repair.
    run_reader = HttpWorkflowRunReader(base_url=base_url)
    diagnoser = RemoteWorkflowDiagnoser(
        base_url=getattr(app_settings, "TROUBLESHOOTING_AGENT_URL", "") or ""
    )

    # The agent's tool loop only designs (discover the palette + schemas, build + structurally
    # self-check a candidate graph) plus diagnose_failure (read-only). The ValidateRepairGate owns
    # persist -> dry-run -> repair -> final test-run, bounded by RepairPolicy (default 3, env
    # WORKFLOW_REPAIR_MAX_ATTEMPTS).
    tools = build_local_tools(catalog, control_plane, run_reader, diagnoser)
    designer = ToolLoopWorkflowDesigner(open_ai_service, tools)
    repairer = LlmWorkflowRepairer(open_ai_service)
    policy = RepairPolicy(max_attempts=app_settings.WORKFLOW_REPAIR_MAX_ATTEMPTS)
    gate = ValidateRepairGate(control_plane, repairer, policy)
    design_workflow_use_case = DesignWorkflowUseCase(designer, gate)
    _design_workflow_use_case = design_workflow_use_case

    deps = {
        "design_workflow_use_case": design_workflow_use_case,
        "agent_graph_builder": AgentGraphBuilder(state_schema=WorkflowDesignState),
        "format_response_node": format_response_node,
        "app_settings": app_settings,
        "checkpointer": checkpointer,
    }
    return _sdk_build_app_container(
        agent_graph=build_graph(deps),
        extra_dependencies={
            "openai_service": open_ai_service,
            "settings": app_settings,
        },
    )
