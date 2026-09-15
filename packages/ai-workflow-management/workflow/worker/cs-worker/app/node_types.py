"""Node registry for the cs-worker.

Every worker_type here is prefixed ``cs_``. That is not cosmetic: this worker leases from
the SAME executor, in the same (blank) tenant, as every other worker registered against
this control plane. Two workers registering the same worker_type would split that queue
between them, and a task would land on whichever leased it first — so a flow built for
the dynamic nodes would sometimes get another worker's, intermittently, with no error
anywhere. Distinct names keep every worker's queue disjoint.

    cs_detect_request      the gate — read the message, validate project/env/CR
    cs_request_log         "what the user sees": the request-level error string
    cs_request_action_log  the action trail
    cs_submit_event_log    the validation steps (carries mdgLogID)
    cs_abap_log            the ABAP-side MD API log
    cs_cloud_logging_search  BTP Cloud Logging (OpenSearch), one instance, all projects
    cs_cancel_request      cancel a CR via RequestorService (the ONLY node here that
                           changes anything)
    cs_summarize_log       the BA-readable analysis
    cs_targets             what this worker is configured for (an operator's smoke test)
"""

from __future__ import annotations

from typing import Any

from worker_sdk import NodeKind
from worker_sdk.layer1_domain.auto_form_mapper import number_field, text_field, textarea_field
from worker_sdk.layer1_domain.entities.node_type_definition import NodeTypeDefinition

from app.config import settings
from app.registry import REGISTRY
from app.detect_request import (
    make_detect_handler as _mk_detect,
    INPUT_SCHEMA as _DETECT_INPUT,
    OUTPUT_SCHEMA as _DETECT_OUTPUT,
)
from app.fetch_log import (
    make_handler as _log_handler,
    input_schema_for as _log_input,
    OUTPUT_SCHEMA as _LOG_OUTPUT,
)
from app.cancel_request import (
    make_handler as _mk_cancel,
    INPUT_SCHEMA as _CANCEL_INPUT,
    OUTPUT_SCHEMA as _CANCEL_OUTPUT,
)
from app.fetch_cls import (
    make_handler as _cls_handler,
    INPUT_SCHEMA as _CLS_INPUT,
    OUTPUT_SCHEMA as _CLS_OUTPUT,
)
from app.summarize_log import (
    make_summary_handler as _mk_summary,
    INPUT_SCHEMA as _SUMMARY_INPUT,
    OUTPUT_SCHEMA as _SUMMARY_OUTPUT,
)

_TAGS = ["smdg", "cs", "log", "support"]


def _log_nt(worker_type: str, name: str, description: str, service: str) -> NodeTypeDefinition:
    return NodeTypeDefinition(
        worker_type=worker_type,
        name=name,
        description=description,
        version=settings.WORKER_VERSION,
        node_class="TECHNICAL",
        icon="FileText",
        color=settings.WORKER_COLOR,
        tags=_TAGS + ["http", "oauth"],
        input_schema=_log_input(service),
        output_schema=_LOG_OUTPUT,
        functions=[],
        handler=_log_handler(service),
        kind=NodeKind.ACTION,
    )


def _targets_handler(inputs: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    """What this worker can reach, and — just as usefully — what it cannot and why.

    Run this node (or hit it in SERVER mode) after adding a project: it answers from the
    same registry the log nodes use, so a green answer here IS the proof the four new
    environment variables landed. ``problems`` names the missing variable rather than
    leaving a target silently absent."""
    return {
        "status": "OK",
        "catalogue": REGISTRY.catalogue(),
        "projects": ", ".join(REGISTRY.projects()),
        "targets": REGISTRY.describe(),
        "problems": REGISTRY.problems(),
        "count": len(REGISTRY.projects()),
        "error": "",
    }


NODE_TYPES = [
    NodeTypeDefinition(
        worker_type="cs_detect_request",
        name="CS: Read request (project + environment + CR)",
        description=(
            "Reads a customer's chat message and decides whether there is anything real to "
            "analyse: which project and environment they mean (validated against the "
            "worker's configured targets, NOT guessed), which change request, and whether "
            "that CR actually exists in that environment. Returns decision=run|stop plus a "
            "ready-to-send sentence for the stop case. Fails OPEN - it stops only on a "
            "definite verdict, so a network blip never blocks a valid request."
        ),
        version=settings.WORKER_VERSION,
        node_class="TECHNICAL",
        icon="ShieldCheck",
        color=settings.WORKER_COLOR,
        tags=_TAGS + ["claude", "gate", "intent"],
        input_schema=_DETECT_INPUT,
        output_schema=_DETECT_OUTPUT,
        functions=[],
        handler=_mk_detect(),
        kind=NodeKind.ACTION,
    ),
    _log_nt(
        "cs_request_log", "CS: RequestLog (what the user sees)",
        "Logs in (client_credentials, credentials from the project registry) and GETs "
        "CommonProcessService/Request(reqID='..')/requestLog - the concise request-level "
        "error string. Addressed by project + environment, not by URL.",
        "request_log",
    ),
    _log_nt(
        "cs_request_action_log", "CS: RequestActionLog",
        "Logs in and GETs CommonProcessService/RequestActionLog for the given change "
        "request. Addressed by project + environment; the endpoint and the OAuth client "
        "both come from the worker's registry.",
        "request_action_log",
    ),
    _log_nt(
        "cs_submit_event_log", "CS: SubmitEventLog",
        "Logs in and GETs CommonProcessService/SubmitEventLog - the per-step validation "
        "trail, and the source of the mdgLogID the ABAP node filters on. Addressed by "
        "project + environment.",
        "submit_event_log",
    ),
    _log_nt(
        "cs_abap_log", "CS: ABAP Log (MD API log)",
        "Logs in and GETs the ABAP-side MD API log (AdminService) filtered by reqID or "
        "mdgLogID. Addressed by project + environment; the entity name is per-landscape "
        "and comes from the registry (MDApiLog, or CeAppLogMsg where that applies).",
        "abap_log",
    ),
    NodeTypeDefinition(
        worker_type="cs_cloud_logging_search",
        name="CS: Cloud Logging Search",
        description=(
            "Searches the BTP Cloud Logging (OpenSearch) instance for the change request "
            "and its mdgLogID. ONE instance serves the whole landscape, so unlike the OData "
            "nodes this one is not per-project: it authenticates with the CLS_* credentials. "
            "UNSUPPORTED read route - see the module docstring in app/fetch_cls.py."
        ),
        version=settings.WORKER_VERSION,
        node_class="TECHNICAL",
        icon="FileSearch",
        color=settings.WORKER_COLOR,
        tags=_TAGS + ["opensearch", "cloud-logging", "btp"],
        input_schema=_CLS_INPUT,
        output_schema=_CLS_OUTPUT,
        functions=[],
        handler=_cls_handler(),
        kind=NodeKind.ACTION,
    ),
    NodeTypeDefinition(
        worker_type="cs_summarize_log",
        name="CS: Summarize Log (Claude, BA format)",
        description=(
            "Reads the collected process logs and writes the BA-facing answer: per failed "
            "validation step, the plain-language cause, whether it is a BA template fix or "
            "an escalation (and to which team + service), and the next action. Non-agentic; "
            "token-rotating CLI."
        ),
        version=settings.WORKER_VERSION,
        node_class="TECHNICAL",
        icon="Sparkles",
        color=settings.WORKER_COLOR,
        tags=_TAGS + ["claude", "summary", "triage"],
        input_schema=_SUMMARY_INPUT,
        output_schema=_SUMMARY_OUTPUT,
        functions=[],
        handler=_mk_summary(),
        kind=NodeKind.ACTION,
    ),
    NodeTypeDefinition(
        worker_type="cs_cancel_request",
        name="CS: Cancel a change request",
        description=(
            "Cancels a CR through RequestorService/cancelRequest on the "
            "srv-process-requestor app - the same action the UI's Cancel button performs "
            "- with the endpoint and the OAuth client taken from the project registry "
            "like every other node here. THE ONLY NODE IN THIS WORKER THAT CHANGES "
            "ANYTHING. Works on every landscape checked, with a plain client_credentials "
            "token. A refusal comes back as DATA with status=OK and ok=false, carrying "
            "the backend's own code - gate the flow on `ok`, never on the HTTP code."
        ),
        version=settings.WORKER_VERSION,
        node_class="TECHNICAL",
        icon="Ban",
        color=settings.WORKER_COLOR,
        tags=_TAGS + ["cancel", "write", "mutating"],
        input_schema=_CANCEL_INPUT,
        output_schema=_CANCEL_OUTPUT,
        functions=[],
        handler=_mk_cancel(),
        kind=NodeKind.ACTION,
    ),
    NodeTypeDefinition(
        worker_type="cs_targets",
        name="CS: Configured projects and environments",
        description=(
            "Lists what this worker is configured for, straight from the registry the log "
            "nodes use - and names the missing variable for any target that is half "
            "configured. Run it after adding a project: a green answer here is the proof "
            "the new environment variables reached the container."
        ),
        version=settings.WORKER_VERSION,
        node_class="TECHNICAL",
        icon="ListChecks",
        color=settings.WORKER_COLOR,
        tags=_TAGS + ["registry", "ops"],
        input_schema=[],
        output_schema=[f.to_dict() for f in [
            text_field("status", "Status", readonly=True),
            text_field("catalogue", "Projects and environments, one line", readonly=True),
            text_field("projects", "Projects", readonly=True),
            number_field("count", "Project count", readonly=True),
            textarea_field("targets", "Every target, with its resolved endpoints", readonly=True),
            textarea_field("problems", "Targets that are configured but not usable, and why",
                           readonly=True),
            text_field("error", "Error (if any)", readonly=True),
        ]],
        functions=[],
        handler=_targets_handler,
        kind=NodeKind.ACTION,
    ),
]
