# Troubleshooting Workflow Agent (SA-1438)

A diagnose-only agent for the **Workflow Service**. Given a run id, it reads the run's
execution log from the Workflow Control Plane and classifies the failure — creation/
configuration, triggering, step execution, agent/service communication, timeout/retry, or
state management — with a root cause and a recommended fix.

Epic **SA-1009** (AI AGENT HUB). Built on `agent-sdk`, copied from `base-agent`.

## Design

`COLLECT → DIAGNOSE → REPORT`

- **COLLECT** — `WorkflowClient.get_execution_log(run_id)` (`GET /api/v1/runs/{run_id}/execution-log`), read-only.
- **DIAGNOSE** — `failure_classifier.classify(...)`, a **pure deterministic** rule engine over the
  execution log (no LLM needed for a verdict). An LLM, if configured, only enriches the narrative.
- **REPORT** — assembles a `DiagnosticReport` (category, severity, root_cause, recommended_fix,
  evidence, failed_node_ids) + a one-line summary.

**Diagnose-only** by design: the agent never restarts/submits/purges. Remediation is a follow-up.

## Layout

- `app/layer1_domain/value_objects/failure_category.py` — the 6 categories (+ UNKNOWN, NO_FAILURE) + fixes.
- `app/layer1_domain/services/failure_classifier.py` — the pure classifier (the core).
- `app/layer1_domain/entities/diagnostic_report.py` — the deliverable entity.
- `app/layer2_application/features/troubleshoot/use_cases/` — collect / diagnose / report.
- `app/layer2_application/graphs/` — the `COLLECT→DIAGNOSE→REPORT` LangGraph flow + nodes.
- `app/layer2_application/tools/diagnose_tool.py` — `diagnose_failure` (cause only) + `suggest_fix` (remediation) (@tool).
- `app/layer4_frameworks/clients/workflow_clients.py` — control-plane client (+ offline mock log).

## Run / test

```bash
pip install -e ".[dev]"   # or: pip install -r requirements.txt  (needs ../agent-sdk)
python -m pytest tests/ -q
python main.py            # SERVER mode on SERVER_PORT (default 36010)
```

Tests are self-contained: deterministic classifier + stubbed control-plane, no live creds.
