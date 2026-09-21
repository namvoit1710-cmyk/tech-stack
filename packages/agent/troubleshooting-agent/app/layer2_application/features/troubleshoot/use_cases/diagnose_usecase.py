import json
from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.entities.diagnostic_report import DiagnosticReport
from app.layer1_domain.services.failure_classifier import (
    classify,
    error_signature,
    redact_secrets,
)
from app.layer1_domain.value_objects.failure_category import (
    EXPLANATION,
    RECOMMENDED_FIX,
    FailureCategory,
)
from app.layer2_application.interfaces.diagnosis_cache_interface import IDiagnosisCache
from app.layer2_application.interfaces.logger_interface import ILogger
from agent_sdk.layer2_application.interfaces.chat_completion_service import (
    IChatCompletionService,
)


@dataclass
class DiagnoseInputDto:
    run_id: str
    execution_log: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiagnoseOutputDto:
    report: DiagnosticReport


_VALID_SEVERITY = {"none", "low", "medium", "high", "critical"}

_LLM_SYSTEM = (
    "You are a Workflow Service troubleshooting classifier. Given a failed run's error "
    "text, classify it into exactly one category from this closed set:\n"
    "- creation_configuration: bad/missing workflow or node definition, missing credentials/config.\n"
    "- triggering: the run never started (trigger/dispatch/webhook/queue).\n"
    "- step_execution: a node handler/expression/script failed (KeyError, mapping, bad input, SyntaxError).\n"
    "- agent_service_communication: a downstream HTTP/gRPC/network call failed (5xx, DNS, refused).\n"
    "- timeout_retry: deadline exceeded or retries exhausted.\n"
    "- state_management: stale/inconsistent run state, checkpoint/aggregate issues.\n"
    "- unknown: genuinely does not fit any category above.\n"
    'Return a JSON object with keys: "category" (one of the values above), "root_cause" '
    '(1-2 sentences), "recommended_fix" (concrete mitigation), "severity" '
    '(none|low|medium|high|critical), "confidence" (0.0-1.0). '
    "Base the verdict only on the provided error text."
)

# Presentation-only prompt: turn the (already-decided, already-redacted) verdict into a plain,
# non-technical explanation for a business user. It must NOT re-classify or change the verdict —
# only rephrase what already happened, grounded strictly in the provided details.
_EXPLAIN_SYSTEM = (
    "You explain a workflow run failure to a non-technical business user. You are given a "
    "diagnosis that has already been decided (category, technical root cause, evidence). Rewrite "
    "it as a short, plain-language explanation of what went wrong.\n"
    "Rules:\n"
    "- 2-4 sentences, warm and clear. No jargon, no HTTP status codes, no stack traces, no "
    "internal IDs (URLs, UUIDs, node ids) unless you restate them in plain words.\n"
    "- If the details name a concrete cause (e.g. missing fields, a service that was "
    "unreachable, a value that didn't match), say it plainly and specifically.\n"
    "- Do NOT invent anything beyond the given details, and do NOT include the fix — the "
    "recommended fix is shown separately.\n"
    "- Return only the explanation text: no preamble, headings, JSON, or markdown."
)


class DiagnoseUseCase:
    """Step 2 — classify the run failure.

    Deterministic-first: the rule-based `classify` reaches a verdict with no LLM whenever a
    known signal matches. When the signal is free-text prose the rules can't place (UNKNOWN)
    and an LLM is configured, escalate to the model to classify that text into the same closed
    category set. Offline/structured runs never touch the LLM, so tests stay reproducible."""

    def __init__(
        self,
        logger: ILogger,
        openai_service: IChatCompletionService | None = None,
        diagnosis_cache: IDiagnosisCache | None = None,
    ):
        self._logger = logger
        self._openai_service = openai_service
        self._cache = diagnosis_cache

    async def execute(self, dto: DiagnoseInputDto) -> DiagnoseOutputDto:
        report = classify(dto.execution_log, run_id=dto.run_id)

        run_error = str(dto.execution_log.get("run_error", "")).strip()
        signature = error_signature(run_error) if run_error else ""

        # Replay: the same error signature always returns the same verdict (A==B) and skips the LLM.
        if signature and self._cache is not None:
            cached = self._cache.get(signature)
            if cached:
                self._logger.info(f"Diagnosis cache hit for {dto.run_id} ({signature})")
                return DiagnoseOutputDto(report=self._from_cache(cached, dto.run_id, report))

        # Escalate to the LLM only when the deterministic rules can't place the free-text error.
        if (
            self._openai_service is not None
            and report.category == FailureCategory.UNKNOWN
            and run_error
        ):
            try:
                report = await self._llm_classify(dto.run_id, run_error, report)
            except Exception as exc:  # LLM is best-effort; keep the deterministic verdict
                self._logger.warning(f"LLM classification failed for {dto.run_id}: {exc}")

        # Humanize: turn the (final) verdict into a plain-language explanation for the user.
        # Presentation-only — never changes the verdict. Cached alongside it below, so the same
        # issue replays the same wording (A==B) and the LLM is called less over time.
        report.explanation = await self._humanize(report, run_error)

        # Remember the verdict so the next caller with the same issue replays it. Never cache
        # UNKNOWN: a no-LLM UNKNOWN would otherwise be replayed forever, suppressing the LLM
        # upgrade once one is configured (with a persistent cache) for the same signature.
        if (
            signature
            and self._cache is not None
            and report.category not in (FailureCategory.NO_FAILURE, FailureCategory.UNKNOWN)
        ):
            self._cache.put(signature, {
                "category": report.category.value,
                "severity": report.severity,
                "root_cause": report.root_cause,
                "recommended_fix": report.recommended_fix,
                "explanation": report.explanation,
                "confidence": report.confidence,
            })

        self._logger.info(
            f"Diagnosed run {dto.run_id}: {report.category.value} "
            f"(severity={report.severity}, confidence={report.confidence})"
        )
        return DiagnoseOutputDto(report=report)

    def _from_cache(
        self, cached: dict[str, Any], run_id: str, base: DiagnosticReport
    ) -> DiagnosticReport:
        try:
            category = FailureCategory(str(cached.get("category", "")).strip().lower())
        except ValueError:
            category = FailureCategory.UNKNOWN
        return DiagnosticReport(
            run_id=run_id,
            category=category,
            severity=cached.get("severity", "high"),
            root_cause=cached.get("root_cause", ""),
            recommended_fix=cached.get("recommended_fix", ""),
            # Older cache entries predate the explanation; fall back to the deterministic template.
            explanation=cached.get("explanation") or EXPLANATION.get(category, ""),
            confidence=float(cached.get("confidence", 0.7)),
            failed_node_ids=base.failed_node_ids,
            evidence=(base.evidence or []) + ["replayed from diagnosis cache"],
        )

    async def _humanize(self, report: DiagnosticReport, run_error: str) -> str:
        """Rewrite the verdict as a plain-language explanation for the end user (best-effort).

        Deterministic baseline (per-category template) when no LLM is configured or the run is
        healthy — so offline/CI still returns a human-readable message. When an LLM is present,
        it rewrites the baseline with the run's specific details. Never changes the verdict."""
        baseline = report.explanation or EXPLANATION.get(report.category, "")
        if self._openai_service is None or report.category == FailureCategory.NO_FAILURE:
            return baseline

        # root_cause / evidence are already redacted by the classifier; mask the raw error too.
        safe_error = redact_secrets(run_error) if run_error else ""
        evidence = "\n".join(f"- {e}" for e in (report.evidence or [])) or "- (none)"
        user_prompt = (
            f"Category: {report.category.value}\n"
            f"Technical root cause: {report.root_cause}\n"
            f"Evidence:\n{evidence}\n"
            + (f"Raw error: {safe_error}\n" if safe_error else "")
            + "\nExplain to the user what went wrong."
        )
        try:
            text = await self._openai_service.get_chat_completion(
                system_prompt=_EXPLAIN_SYSTEM,
                user_prompt=user_prompt,
                json_mode=False,
            )
            return text.strip() or baseline
        except Exception as exc:  # best-effort; fall back to the deterministic template
            self._logger.warning(f"LLM explanation failed for {report.run_id}: {exc}")
            return baseline

    async def _llm_classify(
        self, run_id: str, run_error: str, base: DiagnosticReport
    ) -> DiagnosticReport:
        # Redact secret-shaped tokens before the error text leaves the tenant for the LLM.
        safe_error = redact_secrets(run_error)
        raw = await self._openai_service.get_chat_completion(
            system_prompt=_LLM_SYSTEM,
            user_prompt=f"Run {run_id} failed with:\n{safe_error}",
            json_mode=True,
        )
        data = json.loads(raw)

        # Coerce to the closed set; an off-menu label stays UNKNOWN rather than being fabricated.
        try:
            category = FailureCategory(str(data.get("category", "")).strip().lower())
        except ValueError:
            category = FailureCategory.UNKNOWN
        severity = str(data.get("severity", "")).strip().lower()
        if severity not in _VALID_SEVERITY:
            severity = "high"
        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.7))))
        except (TypeError, ValueError):
            confidence = 0.7

        fix = str(data.get("recommended_fix", "")).strip() or RECOMMENDED_FIX.get(category, "")
        self._logger.info(f"LLM classified {run_id} as {category.value}")
        return DiagnosticReport(
            run_id=run_id,
            category=category,
            severity=severity,
            root_cause=str(data.get("root_cause", "")).strip() or f"{category.value}: {safe_error}",
            recommended_fix=fix,
            explanation=EXPLANATION.get(category, ""),  # baseline; _humanize refines it next
            confidence=confidence,
            failed_node_ids=base.failed_node_ids,
            evidence=(base.evidence or []) + ["classified by gpt-4o from run error text"],
        )
