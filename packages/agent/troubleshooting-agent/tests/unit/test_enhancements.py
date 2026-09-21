"""Tests for the SA-1438 follow-ups: expanded deterministic needles (#2), owner routing (#3),
diagnosis cache replay / A==B (#1), and run_id resolution from workflow id/name (#4)."""
import asyncio

from app.layer1_domain.services.failure_classifier import (
    classify,
    error_signature,
    redact_secrets,
)
from app.layer1_domain.value_objects.failure_category import FailureCategory
from app.layer2_application.features.troubleshoot.use_cases.collect_usecase import (
    CollectInputDto,
    CollectUseCase,
)
from app.layer2_application.features.troubleshoot.use_cases.diagnose_usecase import (
    DiagnoseInputDto,
    DiagnoseUseCase,
)
from app.layer4_frameworks.repositories.diagnosis_cache import DiagnosisCache


class _Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def debug(self, *a, **k): pass


def _log_with_error(msg: str) -> dict:
    """A live-shaped log: no granular events, just the run record's free-text error."""
    return {"summary": {"status": "FAILED"}, "events": [], "run_status": "FAILED", "run_error": msg}


# ── #2 expanded needles: real-world errors now classify deterministically (no LLM) ──────────
def test_needle_getaddrinfo_is_comms():
    r = classify(_log_with_error("Node 'X' failed: [Errno 11001] getaddrinfo failed"))
    assert r.category == FailureCategory.AGENT_SERVICE_COMMUNICATION


def test_needle_missing_credential_is_config():
    r = classify(_log_with_error("'auth_type' is 'bearer' but no credential found on the node"))
    assert r.category == FailureCategory.CREATION_CONFIGURATION


def test_needle_did_not_callback_is_timeout():
    r = classify(_log_with_error("Worker http_request did not callback in time"))
    assert r.category == FailureCategory.TIMEOUT_RETRY


def test_needle_syntaxerror_is_step_execution():
    r = classify(_log_with_error("SyntaxError: invalid character '=>' (U+2192)"))
    assert r.category == FailureCategory.STEP_EXECUTION


# ── #3 owner routing: derived purely from category, so it's consistent everywhere ───────────
def test_owner_routing_per_category():
    assert classify(_log_with_error("no credential found")).to_dict()["owner"] == "user"
    assert classify(_log_with_error("getaddrinfo failed")).to_dict()["owner"] == "ops"
    healthy = {"summary": {"node_counts": {"failed": 0}}, "events": []}
    assert classify(healthy).to_dict()["owner"] == "none"


# ── M1 redaction: secrets are masked before run text is sent to the LLM ─────────────────────
def test_redact_secrets_masks_credentials_but_keeps_signal():
    fake_token = "ghp_" + "EXAMPLEtoken1234567890abcdefghijkl"
    raw = (
        f"Node auth failed: bearer {fake_token}; "
        "connecting to postgres://user:s3cr3t@db.internal:5432/wf; "
        "config password=hunter2 and getaddrinfo failed"
    )
    out = redact_secrets(raw)
    # secrets gone
    assert fake_token not in out
    assert "s3cr3t" not in out
    assert "hunter2" not in out
    # classification signal preserved
    assert "getaddrinfo failed" in out


def test_redact_secrets_noop_on_clean_text():
    assert redact_secrets("worker did not callback in time") == "worker did not callback in time"
    assert redact_secrets("") == ""


# ── M-1 (felix review): root_cause + evidence are redacted on the deterministic path too ─────
def test_classify_redacts_secrets_in_root_cause_and_evidence():
    log = _log_with_error("connecting to postgres://user:s3cr3t@db.internal:5432/wf failed: connection refused")
    r = classify(log).to_dict()
    assert "s3cr3t" not in r["root_cause"]
    assert not any("s3cr3t" in e for e in r["evidence"])
    assert r["category"] == FailureCategory.AGENT_SERVICE_COMMUNICATION.value  # signal preserved


# ── M-2 (felix review): a terminal status / run-record error is a failure, not NO_FAILURE ────
def test_terminal_status_is_not_reported_healthy():
    # No NodeFailed event, no failed count — only a terminal status + run-record error.
    log = {"summary": {}, "events": [], "run_status": "TIMED_OUT",
           "run_error": "worker did not callback in time"}
    r = classify(log)
    assert r.category != FailureCategory.NO_FAILURE
    assert r.category == FailureCategory.TIMEOUT_RETRY


def test_terminal_status_without_error_text_still_fails():
    log = {"summary": {"status": "CANCELED"}, "events": []}
    assert classify(log).category != FailureCategory.NO_FAILURE


# ── #1 error signature is stable and run-agnostic ───────────────────────────────────────────
def test_error_signature_masks_run_specifics():
    a = error_signature("Run 111 at be1a29e1-f730-4075-8415-023db5e1a061 failed: timeout")
    b = error_signature("Run 999 at 97b383e3-4368-44c6-a76f-fd19092de471 failed: timeout")
    assert a == b  # same issue, different run ids/numbers → same signature


# ── #1 cache replay: same issue → same verdict for everyone, LLM called at most once ────────
class _CountingLLM:
    """Counts classify vs. explain calls separately. The classifier path asks for JSON
    (json_mode=True); the humanizer asks for plain prose (json_mode=False)."""

    def __init__(self):
        self.classify_calls = 0
        self.explain_calls = 0

    @property
    def calls(self):
        return self.classify_calls + self.explain_calls

    async def get_chat_completion(self, system_prompt, user_prompt, json_mode=True):
        if json_mode:
            self.classify_calls += 1
            return (
                '{"category": "state_management", "root_cause": "novel state bug", '
                '"recommended_fix": "reconcile aggregate", "severity": "high", "confidence": 0.8}'
            )
        self.explain_calls += 1
        return "In plain terms, the run got into a bad internal state and couldn't continue."


def test_cache_replays_verdict_and_skips_llm():
    llm = _CountingLLM()
    cache = DiagnosisCache()  # in-memory
    # A novel error that no needle matches → forces the LLM path the first time.
    log = _log_with_error("gizmo subsystem returned frobnitz code 42")

    async def run(run_id):
        uc = DiagnoseUseCase(_Log(), openai_service=llm, diagnosis_cache=cache)
        return (await uc.execute(DiagnoseInputDto(run_id=run_id, execution_log=log))).report

    first = asyncio.run(run("user-A-run"))   # computes via LLM (classify + humanize)
    calls_after_first = llm.calls
    second = asyncio.run(run("user-B-run"))  # replays from cache

    assert llm.classify_calls == 1           # classified exactly once, ever
    assert llm.calls == calls_after_first    # user B added NO LLM calls (full replay)
    # A == B on the verdict (category/fix/severity) AND the explanation, regardless of who asked.
    assert first.category == second.category == FailureCategory.STATE_MANAGEMENT
    assert first.recommended_fix == second.recommended_fix
    assert first.severity == second.severity
    assert first.explanation == second.explanation and first.explanation
    assert "replayed from diagnosis cache" in second.evidence


# ── L-2 (felix review): a no-LLM UNKNOWN is NOT cached, so a later LLM run can still upgrade it ─
def test_unknown_verdict_is_not_cached():
    cache = DiagnosisCache()
    novel = _log_with_error("gizmo subsystem returned frobnitz code 42")  # matches no needle

    # First run: no LLM → UNKNOWN. Must not be written to the cache.
    asyncio.run(DiagnoseUseCase(_Log(), diagnosis_cache=cache).execute(
        DiagnoseInputDto(run_id="r1", execution_log=novel)))

    llm = _CountingLLM()
    second = asyncio.run(DiagnoseUseCase(_Log(), openai_service=llm, diagnosis_cache=cache).execute(
        DiagnoseInputDto(run_id="r2", execution_log=novel)))
    assert llm.classify_calls == 1  # not suppressed by a stale cached UNKNOWN
    assert second.report.category == FailureCategory.STATE_MANAGEMENT


# ── #4 resolve run_id from workflow name when run_id is unknown ──────────────────────────────
class _ResolvingClient:
    async def resolve_run_id(self, workflow_id="", workflow_name=""):
        return "resolved-run-7" if (workflow_id or workflow_name) else ""

    async def get_execution_log(self, run_id, offset=0, include_children=True):
        return _log_with_error("getaddrinfo failed") | {"run_id": run_id}


def test_collect_resolves_run_id_from_workflow_name():
    out = asyncio.run(
        CollectUseCase(_Log(), _ResolvingClient()).execute(
            CollectInputDto(workflow_name="jira hook test")
        )
    )
    assert out.run_id == "resolved-run-7"
    assert out.execution_log["run_error"] == "getaddrinfo failed"


def test_collect_returns_empty_when_nothing_to_resolve():
    out = asyncio.run(
        CollectUseCase(_Log(), _ResolvingClient()).execute(CollectInputDto())
    )
    assert out.run_id == ""
    assert out.execution_log == {}


# ── Humanizer: the user-facing explanation is plain language, general across categories ──────
from app.layer1_domain.value_objects.failure_category import EXPLANATION  # noqa: E402


class _EchoExplainLLM:
    """Classifies (JSON) like _CountingLLM, but the humanizer (plain text) returns a fixed
    friendly sentence and records the prompt it was given (to assert no-leak / grounding)."""

    def __init__(self):
        self.explain_prompt = ""

    async def get_chat_completion(self, system_prompt, user_prompt, json_mode=True):
        if json_mode:
            return (
                '{"category": "step_execution", "root_cause": "a node failed", '
                '"recommended_fix": "fix the node", "severity": "high", "confidence": 0.8}'
            )
        self.explain_prompt = user_prompt
        return "  A step in your workflow couldn't process the data it was given.  "


def test_humanizer_rewrites_a_deterministic_verdict_in_plain_language():
    # A known-needle failure → deterministic category (no classify LLM call). The humanizer
    # still runs and replaces the message with plain language.
    llm = _EchoExplainLLM()
    out = asyncio.run(
        DiagnoseUseCase(_Log(), openai_service=llm).execute(
            DiagnoseInputDto(run_id="r1", execution_log=_log_with_error("getaddrinfo failed"))
        )
    )
    assert out.report.category == FailureCategory.AGENT_SERVICE_COMMUNICATION  # verdict unchanged
    assert out.report.explanation == "A step in your workflow couldn't process the data it was given."


def test_humanizer_input_is_redacted_and_grounded():
    llm = _EchoExplainLLM()
    secret_log = _log_with_error("connecting to postgres://user:s3cr3t@db/wf: getaddrinfo failed")
    asyncio.run(
        DiagnoseUseCase(_Log(), openai_service=llm).execute(
            DiagnoseInputDto(run_id="r1", execution_log=secret_log)
        )
    )
    assert "s3cr3t" not in llm.explain_prompt          # secret never reaches the LLM
    assert "getaddrinfo failed" in llm.explain_prompt  # the real signal is still grounded


def test_explanation_falls_back_to_template_without_llm():
    # No LLM configured → the deterministic per-category template, never an empty message.
    out = asyncio.run(
        DiagnoseUseCase(_Log()).execute(
            DiagnoseInputDto(run_id="r1", execution_log=_log_with_error("getaddrinfo failed"))
        )
    )
    assert out.report.explanation == EXPLANATION[FailureCategory.AGENT_SERVICE_COMMUNICATION]


def test_healthy_run_explanation_is_reassuring_and_skips_llm():
    llm = _EchoExplainLLM()
    healthy = {"summary": {"node_counts": {"failed": 0}}, "events": []}
    out = asyncio.run(
        DiagnoseUseCase(_Log(), openai_service=llm).execute(
            DiagnoseInputDto(run_id="ok", execution_log=healthy)
        )
    )
    assert out.report.category == FailureCategory.NO_FAILURE
    assert out.report.explanation == EXPLANATION[FailureCategory.NO_FAILURE]
    assert llm.explain_prompt == ""  # healthy run → no LLM spend
