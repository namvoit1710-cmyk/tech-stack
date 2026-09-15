"""Node registry for the SMDG QC Bot worker (v2) — SA-2494.

One node type, ``request_event_data``: resolve an OAuth credential from the workflow
environment (SA-2365 ``$env`` credential), GET CommonProcessService/RequestEventData by
reqID, and parse the nested DuplicationCheck / DuplicationCheckWithCondition fields into a
readable message. See app/fetch_dupcheck.py.
"""

from __future__ import annotations

from worker_sdk import NodeKind
from worker_sdk.layer1_domain.entities.node_type_definition import NodeTypeDefinition

from app.config import settings
from app.fetch_dupcheck import (
    make_handler as _dupe_handler,
    INPUT_SCHEMA as _DUPE_INPUT,
    OUTPUT_SCHEMA as _DUPE_OUTPUT,
)
from app.fetch_scopeauth import (
    make_handler as _scope_handler,
    INPUT_SCHEMA as _SCOPE_INPUT,
    OUTPUT_SCHEMA as _SCOPE_OUTPUT,
)
from app.fetch_onprem import (
    make_handler as _onprem_handler,
    INPUT_SCHEMA as _ONPREM_INPUT,
    OUTPUT_SCHEMA as _ONPREM_OUTPUT,
)


NODE_TYPES = [
    NodeTypeDefinition(
        worker_type="qc_duplication_check",
        name="SMDG Duplication Check",
        description=(
            "Takes a reqID, GETs CommonProcessService/RequestEventData and parses the "
            "nested templateConfigData.templateData.DuplicationCheck / "
            "DuplicationCheckWithCondition arrays into a readable message. Authentication "
            "comes from the flow's environment/Section — the node has no credential or "
            "token-URL fields."
        ),
        version=settings.WORKER_VERSION,
        node_class="TECHNICAL",
        icon="FileText",
        color=settings.WORKER_COLOR,
        tags=["smdg", "qc", "duplication"],
        input_schema=_DUPE_INPUT,
        output_schema=_DUPE_OUTPUT,
        functions=[],
        handler=_dupe_handler(),
        kind=NodeKind.ACTION,
    ),
    NodeTypeDefinition(
        worker_type="qc_scope_auth",
        name="SMDG Scope Authorization",
        description=(
            "Takes an account (userID) and GETs AdminUserService/UserScopeAuthObjects "
            "(srv-user), returning the account's scope authorizations — scopeID (group), "
            "authObjectID and authObjectValue — as a readable message. Active-only by "
            "default. Authentication comes from the flow's environment/Section."
        ),
        version=settings.WORKER_VERSION,
        node_class="TECHNICAL",
        icon="ShieldCheck",
        color=settings.WORKER_COLOR,
        tags=["smdg", "qc", "authorization", "scope"],
        input_schema=_SCOPE_INPUT,
        output_schema=_SCOPE_OUTPUT,
        functions=[],
        handler=_scope_handler(),
        kind=NodeKind.ACTION,
    ),
    NodeTypeDefinition(
        worker_type="qc_onprem_data",
        name="SMDG On-Premise Data",
        description=(
            "Takes objectType + onPremiseObjectID (a JSON key string) and POSTs "
            "MainService/getObjectOnPremiseData to fetch the object's full on-premise "
            "data, returned as a readable message plus the raw response. Handles both an "
            "Object ID (e.g. BusinessPartner {\"objectID\":\"..\"}) and an ERP number "
            "(e.g. Product {\"product\":\"..\"}). Authentication comes from the flow's "
            "environment/Section."
        ),
        version=settings.WORKER_VERSION,
        node_class="TECHNICAL",
        icon="Database",
        color=settings.WORKER_COLOR,
        tags=["smdg", "qc", "onpremise", "object"],
        input_schema=_ONPREM_INPUT,
        output_schema=_ONPREM_OUTPUT,
        functions=[],
        handler=_onprem_handler(),
        kind=NodeKind.ACTION,
    ),
]
