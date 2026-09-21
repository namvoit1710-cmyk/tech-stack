from typing import Any

from app.layer1_domain.services.failure_classifier import classify
from app.layer2_application.interfaces.register_tool import tool

# The diagnosis answers "what failed / why" — deliberately WITHOUT recommended_fix, so a
# user asking about the error gets the cause only. suggest_fix owns the "how to fix" half.
_DIAGNOSIS_FIELDS = (
    "run_id", "category", "owner", "severity",
    "root_cause", "explanation", "confidence", "failed_node_ids", "evidence",
)


@tool
def diagnose_failure(execution_log: dict[str, Any], run_id: str = "") -> dict[str, Any]:
    """Diagnose WHY a Workflow Control Plane run failed. Use this when the user asks about
    the error, the cause, or what went wrong. Returns the failure category, root cause,
    severity, owner, and evidence — but NOT how to fix it. For remediation call suggest_fix."""
    verdict = classify(execution_log, run_id=run_id).to_dict()
    return {key: verdict[key] for key in _DIAGNOSIS_FIELDS}


@tool
def suggest_fix(execution_log: dict[str, Any], run_id: str = "") -> dict[str, Any]:
    """Suggest HOW to fix a failed Workflow Control Plane run. Use this when the user asks
    how to fix / resolve / mitigate the failure. Returns the recommended fix and who should
    act (owner), keyed to the diagnosed category. For the cause itself call diagnose_failure."""
    verdict = classify(execution_log, run_id=run_id).to_dict()
    return {
        "run_id": verdict["run_id"],
        "category": verdict["category"],
        "owner": verdict["owner"],
        "recommended_fix": verdict["recommended_fix"],
    }
