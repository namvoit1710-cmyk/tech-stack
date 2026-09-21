from app.layer1_domain.entities.validation_rule_state import ValidationRuleState
from app.layer4_frameworks.graph import build_validation_file_graph
from app.layer4_frameworks.file_service import FileService
from app.layer4_frameworks.csv_services.column_operations import ColumnTransformationExecutor
from app.layer4_frameworks.planner_worker import Planner
from app.layer4_frameworks.header_inference_service import HeaderInferenceService
from app.layer4_frameworks.validation_result_summary_service import ValidationResultSummaryService
from app.layer4_frameworks.polar_generator_adapter import PolarGeneratorAdapter
from app.layer4_frameworks.governance_service import GovernanceService
from app.layer4_frameworks.config.settings_adapter import AppSettingsAdapter
from app.layer2_application.features.validation_rule.use_cases.generate_polar_use_case import GeneratePolarUseCase
from app.layer2_application.features.validation_rule.use_cases.execute_validation_use_case import ExecuteValidationUseCase
from app.layer2_application.features.validation_rule.use_cases.validate_by_rules_use_case import ValidateByRulesUseCase
from agent_sdk import build_app_container as _sdk_build_app_container
from agent_sdk import AgentGraphBuilder, OpenAIService, format_response_node


def build_app_container() -> dict:
    settings = AppSettingsAdapter()
    agent_type = settings.AGENT_TYPE

    open_ai_service = OpenAIService()
    governance = GovernanceService()

    # Two dedicated, flag-agnostic use-cases (Layer 2) composed over ports.
    generate_polar_use_case = GeneratePolarUseCase(
        polar_generator=PolarGeneratorAdapter(),
        governance=governance,
        agent_type=agent_type,
    )
    execute_validation_use_case = ExecuteValidationUseCase(
        file_service=FileService(),
        header_inference=HeaderInferenceService(),
        planner=Planner(),
        column_executor=ColumnTransformationExecutor(),
        summary_service=ValidationResultSummaryService(open_ai_service),
        governance=governance,
        agent_type=agent_type,
    )
    validate_by_rules_use_case = ValidateByRulesUseCase(execute_validation_use_case, agent_type)

    deps = {
        "generate_polar_use_case": generate_polar_use_case,
        "execute_validation_use_case": execute_validation_use_case,
        "agent_graph_builder": AgentGraphBuilder(state_schema=ValidationRuleState),
        "format_response_node": format_response_node,
    }
    container = _sdk_build_app_container(
        agent_graph=build_validation_file_graph(deps)
    )
    # Expose the dedicated use-cases so the REST controllers (Layer 3) can resolve them.
    container["generate_polar_use_case"] = generate_polar_use_case
    container["execute_validation_use_case"] = execute_validation_use_case
    container["validate_by_rules_use_case"] = validate_by_rules_use_case
    return container
