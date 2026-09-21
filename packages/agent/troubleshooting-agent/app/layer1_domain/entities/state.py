from agent_sdk.layer1_domain.entities.agent_state import AgentBaseState

from app.layer1_domain.entities.diagnostic_report import DiagnosticReport


class MainState(AgentBaseState, total=False):
    run_id: str
    workflow_id: str
    workflow_name: str
    execution_log: dict
    diagnosis: DiagnosticReport  # written by diagnose_node, read by report_node — must be a channel
    report: dict
