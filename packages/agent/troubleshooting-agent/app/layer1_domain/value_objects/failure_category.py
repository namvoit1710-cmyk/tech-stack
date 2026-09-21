from enum import StrEnum


class FailureCategory(StrEnum):
    """The failure classes SA-1438 asks the agent to troubleshoot, plus the two
    honest non-failure verdicts. Closed set — the classifier never invents a class."""

    CREATION_CONFIGURATION = "creation_configuration"
    TRIGGERING = "triggering"
    STEP_EXECUTION = "step_execution"
    AGENT_SERVICE_COMMUNICATION = "agent_service_communication"
    TIMEOUT_RETRY = "timeout_retry"
    STATE_MANAGEMENT = "state_management"
    UNKNOWN = "unknown"          # failed, but no signal matched — reported, never fabricated
    NO_FAILURE = "no_failure"    # the run is healthy


# Canned mitigation guidance per category (the "clear mitigation steps" the ACs require).
RECOMMENDED_FIX: dict[FailureCategory, str] = {
    FailureCategory.CREATION_CONFIGURATION: (
        "Validate the workflow/node definition and required connector config before the run: "
        "check missing/invalid fields, credentials, and that referenced services are configured."
    ),
    FailureCategory.TRIGGERING: (
        "The run never reached a started state. Verify the trigger/dispatch path (event binding, "
        "queue connectivity, and that the control plane accepted the run request)."
    ),
    FailureCategory.STEP_EXECUTION: (
        "A node handler/expression failed. Inspect the failing node's input data and expression/"
        "handler logic; fix the mapping or guard the expression, then re-run that node."
    ),
    FailureCategory.AGENT_SERVICE_COMMUNICATION: (
        "A downstream agent/service call failed (HTTP 5xx / connection / gRPC). Check the target "
        "service health and network reachability; add/verify retry with backoff on the call."
    ),
    FailureCategory.TIMEOUT_RETRY: (
        "The step exceeded its deadline or exhausted retries. Increase the timeout for slow steps "
        "and/or tune the retry policy (attempts + backoff); confirm the worker is not stuck."
    ),
    FailureCategory.STATE_MANAGEMENT: (
        "Run state looks inconsistent/stale. Reconcile the run aggregate with the event log / "
        "checkpoint store; ensure NodeOutputProduced events are applied in order."
    ),
    FailureCategory.UNKNOWN: (
        "Run failed but no known signal matched. Pull the full node data "
        "(/nodes/{node_id}/data?side=output) and raw error to classify manually."
    ),
    FailureCategory.NO_FAILURE: "",
}


# Plain-language, non-technical narrative per category — what a business user actually reads.
# This is the DETERMINISTIC baseline: offline / no-LLM, the report still carries a human-readable
# message. When an LLM is configured, the humanizer rewrites this with the run's specific details
# (see DiagnoseUseCase._humanize) — but it never changes the verdict, only the wording.
EXPLANATION: dict[FailureCategory, str] = {
    FailureCategory.CREATION_CONFIGURATION: (
        "The workflow couldn't run because something in its setup is missing or incorrect — "
        "for example a required field, a connection, or a credential wasn't provided. It needs "
        "to be configured correctly before it can run again."
    ),
    FailureCategory.TRIGGERING: (
        "The workflow never actually started. Whatever was meant to kick it off — a trigger, "
        "schedule, or event — didn't reach it, so no steps ran."
    ),
    FailureCategory.STEP_EXECUTION: (
        "One of the steps in the workflow hit a problem while processing the data. Usually the "
        "input didn't match what that step expected, so it couldn't finish."
    ),
    FailureCategory.AGENT_SERVICE_COMMUNICATION: (
        "The workflow tried to reach another service and couldn't get a proper response. The "
        "other service may have been down, unreachable, or returned an error."
    ),
    FailureCategory.TIMEOUT_RETRY: (
        "A step took too long and ran out of time (or ran out of retry attempts) before it "
        "could finish, so the workflow stopped waiting."
    ),
    FailureCategory.STATE_MANAGEMENT: (
        "The workflow's saved progress became inconsistent, so it couldn't reliably continue "
        "from where it left off. This is an internal issue that needs an engineer to look at."
    ),
    FailureCategory.UNKNOWN: (
        "The workflow failed, but the cause doesn't match any of the known patterns, so we "
        "can't explain it precisely yet. The technical details are attached for a closer look."
    ),
    FailureCategory.NO_FAILURE: "Good news — this run finished without any failures.",
}


# Who should act on each class of failure — so the report routes itself.
#   user → the workflow author fixes their config/logic
#   ops  → operations tune infra/service health/timeouts/dispatch
#   dev  → a platform bug needing engineering (state, or unclassifiable)
OWNER: dict[FailureCategory, str] = {
    FailureCategory.CREATION_CONFIGURATION: "user",
    FailureCategory.TRIGGERING: "ops",
    FailureCategory.STEP_EXECUTION: "user",
    FailureCategory.AGENT_SERVICE_COMMUNICATION: "ops",
    FailureCategory.TIMEOUT_RETRY: "ops",
    FailureCategory.STATE_MANAGEMENT: "dev",
    FailureCategory.UNKNOWN: "dev",
    FailureCategory.NO_FAILURE: "none",
}
