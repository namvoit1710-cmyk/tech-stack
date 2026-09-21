"""The two tools split one classifier into cause-only vs fix-only, so the LLM can pick
the one that matches the user's question."""
from app.layer2_application.tools.diagnose_tool import diagnose_failure, suggest_fix

# A failed run whose free-text error is a known needle → deterministic verdict, no LLM.
_FAILED_LOG = {"summary": {"status": "FAILED"}, "events": [], "run_error": "getaddrinfo failed"}


def test_diagnose_failure_returns_cause_without_fix():
    out = diagnose_failure(_FAILED_LOG, run_id="r-1")
    assert out["category"] == "agent_service_communication"
    assert out["root_cause"]
    assert "recommended_fix" not in out  # cause only — the fix is suggest_fix's job


def test_suggest_fix_returns_remediation_only():
    out = suggest_fix(_FAILED_LOG, run_id="r-1")
    assert out["recommended_fix"]
    assert out["owner"] == "ops"
    assert "root_cause" not in out  # remediation only — the cause is diagnose_failure's job
