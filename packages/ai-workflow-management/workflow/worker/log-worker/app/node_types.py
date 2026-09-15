"""Node registry for the SMDG log-worker.

Two self-authenticating log-fetcher node types (OAuth login + OData GET happen
INSIDE each node). All endpoint knobs are configurable fields (see fetch_log).
"""

from __future__ import annotations

from typing import Any

from worker_sdk import NodeKind
from worker_sdk.layer1_domain.entities.node_type_definition import NodeTypeDefinition

from app.config import settings
from app.fetch_log import (
    make_handler as _log_handler,
    input_schema_for as _log_input,
    OUTPUT_SCHEMA as _LOG_OUTPUT,
)
from app.fetch_cls import (
    make_handler as _cls_handler,
    INPUT_SCHEMA as _CLS_INPUT,
    OUTPUT_SCHEMA as _CLS_OUTPUT,
)
from app.fetch_cf_dashboard import (
    make_handler as _cf_handler,
    INPUT_SCHEMA as _CF_INPUT,
    OUTPUT_SCHEMA as _CF_OUTPUT,
)
# NOTE: the reqID extractor (extract_reqid) MOVED to the claude-worker so it reuses the
# engine's token-rotating _call_claude_cli (account rotation on rate-limit/auth). It is
# NO LONGER registered here — app/extract.py is retired.


def _log_nt(worker_type: str, name: str, description: str, service: str) -> NodeTypeDefinition:
    return NodeTypeDefinition(
        worker_type=worker_type,
        name=name,
        description=description,
        version=settings.WORKER_VERSION,
        node_class="TECHNICAL",
        icon="FileText",
        color=settings.WORKER_COLOR,
        tags=["smdg", "log", "http", "oauth"],
        input_schema=_log_input(service),
        output_schema=_LOG_OUTPUT,
        functions=[],
        handler=_log_handler(service),
        kind=NodeKind.ACTION,
    )


NODE_TYPES = [
    _log_nt(
        "request_action_log", "SMDG: RequestActionLog",
        "Logs in (client_credentials) and GETs CommonProcessService/RequestActionLog "
        "for the given reqID. URL / token URL / $select / $orderby are configurable "
        "fields. client_id/client_secret come from the local secrets file.",
        "request_action_log",
    ),
    _log_nt(
        "submit_event_log", "SMDG: SubmitEventLog",
        "Logs in (client_credentials) and GETs CommonProcessService/SubmitEventLog "
        "for the given reqID. URL / token URL / $select / $orderby are configurable "
        "fields. client_id/client_secret come from the local secrets file.",
        "submit_event_log",
    ),
    _log_nt(
        "request_log", "SMDG: RequestLog",
        "Logs in (client_credentials) and GETs CommonProcessService/Request(reqID=..)/"
        "requestLog for the given reqID — the concise request-level error string. URL base "
        "/ token URL are configurable fields; client_id/client_secret from env or secrets.",
        "request_log",
    ),
    _log_nt(
        "abap_log", "SMDG: ABAP Log (MDApiLog)",
        "Logs in (client_credentials) and GETs AdminService/MDApiLog?$filter=reqID eq '..' "
        "— the ABAP-side MD API log (mdgLogID / objectType / step / status / messageLog). "
        "Served by the srv-config-admin app; set api_url + token_url per env on the node.",
        "abap_log",
    ),
    # BTP Cloud Logging (OpenSearch). Sibling source to the two OData nodes above:
    # same output shape, different backend. See app/fetch_cls.py for the support
    # caveat — SAP does not officially expose a read API, so this rides the
    # dashboards route and is unverified until cls_probe.py passes on the instance.
    NodeTypeDefinition(
        worker_type="cloud_logging_search",
        name="SMDG: Cloud Logging Search",
        description=(
            "Searches the BTP Cloud Logging (OpenSearch) instance. Basic-auth login with "
            "the service key's dashboards credentials from the local secrets file, then an "
            "OpenSearch query. Index / time window / search field are configurable; a raw "
            "query DSL field overrides them. UNSUPPORTED read route — see module docstring."
        ),
        version=settings.WORKER_VERSION,
        node_class="TECHNICAL",
        icon="FileSearch",
        color=settings.WORKER_COLOR,
        tags=["smdg", "log", "opensearch", "cloud-logging", "btp"],
        input_schema=_CLS_INPUT,
        output_schema=_CLS_OUTPUT,
        functions=[],
        handler=_cls_handler(),
        kind=NodeKind.ACTION,
    ),
    # SAP's "CF Requests and Logs" dashboard, as a node. Contexts + columns are lifted
    # verbatim from that dashboard's own saved searches, so a flow sees what the UI sees.
    NodeTypeDefinition(
        worker_type="cf_requests_logs_search",
        name="SMDG: CF Requests & Logs",
        description=(
            "Queries the BTP Cloud Logging 'CF Requests and Logs' dashboard data by time "
            "window + context (requests / application / runtime / all). Filters and output "
            "columns match SAP's saved searches on logs-cfsyslog-*. Lucene query plus an "
            "optional raw DSL override. Basic auth as an OpenSearch internal user."
        ),
        version=settings.WORKER_VERSION,
        node_class="TECHNICAL",
        icon="Activity",
        color=settings.WORKER_COLOR,
        tags=["smdg", "log", "opensearch", "cloud-logging", "cf", "btp"],
        input_schema=_CF_INPUT,
        output_schema=_CF_OUTPUT,
        functions=[],
        handler=_cf_handler(),
        kind=NodeKind.ACTION,
    ),
]
