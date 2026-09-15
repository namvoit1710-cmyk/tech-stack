"""WorkerNodeProvider — exposes workers registered with the executor."""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.layer1_domain.value_objects.node_kind import NodeKind, safe_kind_value
from app.layer1_domain.value_objects.port import has_io_port
from app.layer1_domain.value_objects.worker_ref import WorkerRef
from app.layer2_application.providers.worker_freshness import is_fresh, parse_ts
from app.layer2_application.interfaces.worker_registry_client_interface import (
    IWorkerRegistryClient,
)
from app.layer2_application.contracts import (
    FormField,
    NodeCapability,
    NodeDefinitionDetail,
    NodeDefinitionSummary,
    NodePort,
    NodePorts,
    NodeRuntime,
    NodeVisibility,
)


logger = logging.getLogger(__name__)

_HEALTHY_STATUSES = (None, "", "healthy", "active")

# Design-time credential affinity per worker_type, in the vault CredentialKind
# vocabulary (the FE picker filters the tenant's credential list by these — see
# credential-node-map.ts / credential-filter.ts). The catalog is authoritative:
# a worker listed here gets its picker from these kinds; workers not listed keep
# the FE's static fallback allow-list. Only seed kinds verified against the
# worker's own credential-consumption code — wrong kinds filter the picker to
# empty, which is worse than the fallback.
_CREDENTIAL_AFFINITY: dict[str, list[str]] = {
    # email-worker: SMTP username/password (generic-basic-auth blob) and Graph
    # client-credentials (generic-oauth2-app blob) — see its execute_task_usecase.
    "email": ["BASIC_AUTH", "OAUTH2_APPLICATION"],
    # ms-teams-worker: vault webhook_url credential only (T5 removed inline URLs).
    # SA-1558: kind is WEBHOOK_URL (uppercase) — the canonical credentials-management
    # kind for ms-teams-webhook. The earlier lowercase "webhook_url" (SA-1548) never
    # matched the picker's case-sensitive filter, so the picker was empty.
    "ms_teams": ["WEBHOOK_URL"],
    # ms_teams_meeting (SA-1549): Graph client-credentials (generic-oauth2-app
    # blob) — Calendars.ReadWrite. A NEW worker_type, absent from the FE fallback
    # set, so seeding here is REQUIRED for the picker to render at all.
    "ms_teams_meeting": ["OAUTH2_APPLICATION"],
    # ms_teams_channel (SA-1550): Graph client-credentials — Channel.Create /
    # TeamMember.ReadWrite.All / Team.ReadBasic.All. New worker_type; seeding required.
    "ms_teams_channel": ["OAUTH2_APPLICATION"],
    # SA-1563: these three consume a credential (credential_ref) but were only in the FE
    # fallback set (credential-node-map), so their picker rendered with kinds:[] = show
    # ALL tenant credentials, unfiltered. Seed the real credentialKind(s) so the picker
    # lists only compatible credentials (same fix shape as SA-1558).
    # agent-worker: extracts an api_key from the credential (generic-api-key = API_KEY).
    "agent": ["API_KEY"],
    # database-connection-worker: a bundled generic-database (DATABASE_CONNECTION) or a
    # basic user/pass login (BASIC_AUTH). NOTE: the worker registers its catalog
    # worker_type as "database-connection" (HYPHEN, from the deployment name) — NOT the
    # config WORKER_TYPE "database_connection". The affinity is matched on the registered
    # worker_type, so the key MUST be hyphenated or the seed silently misses (verified
    # live: the FE fallback set's "database_connection"/"database" also never matched, so
    # this node had no working picker until now).
    "database-connection": ["DATABASE_CONNECTION", "BASIC_AUTH"],
    # http-request-worker: generic HTTP client — auth_type is bearer/basic/api_key and
    # tokens may be PATs/JWTs/OAuth2. A curated broad set beats the unfiltered show-all.
    "http_request": ["API_KEY", "BASIC_AUTH", "PERSONAL_ACCESS_TOKEN", "JWT", "OAUTH2_APPLICATION"],
    # claude-worker: claude_implement / claude_code clone a Git repo (GitHub OR GitLab)
    # and open a PR/MR using a git PAT (kind PERSONAL_ACCESS_TOKEN). Seed the picker so the
    # operator can select the git credential in the UI; the worker resolves it from
    # credential_ref → the lent vault credential (_git_token_from_credential). Without this
    # the node has no picker (SA-2415: credential_ref could only be set via the API).
    "claude_implement": ["PERSONAL_ACCESS_TOKEN"],
    "claude_code": ["PERSONAL_ACCESS_TOKEN"],
    # req2tpl-worker: nine req2tpl_* nodes, but only these TWO talk to Jira (pull the
    # ticket's workbooks down, push the built template back) and they use the same vault
    # jira-api-token as the jira_* family. Seeded EXACTLY rather than by a "req2tpl_"
    # prefix on purpose: the other seven drive a local CLI and consume no credential, so
    # a family prefix would render a credential picker on nodes that have nothing to
    # authenticate.
    "req2tpl_fetch_inputs": ["API_KEY"],
    "req2tpl_attach_result": ["API_KEY"],
}

# Multi-node connectors register MANY worker_types from ONE process (e.g. the
# jira-worker's 22 ``jira_*`` nodes), all sharing ONE vault credential. Rather than
# repeat every worker_type above, a family PREFIX seeds them together. An exact
# match in _CREDENTIAL_AFFINITY still wins over a prefix.
_CREDENTIAL_AFFINITY_PREFIXES: dict[str, list[str]] = {
    # jira-worker: every jira_* node uses the vault jira-api-token credential
    # (provider JIRA, kind API_KEY — the credentials-management adapter's kind).
    # A NEW worker family absent from the FE fallback set, so seeding here is
    # REQUIRED for the picker to render at all (same reason as ms_teams_meeting).
    "jira_": ["API_KEY"],
}


def _credential_kinds_for(worker_type: str) -> list[str]:
    """Resolve the design-time credential kinds for a worker_type.

    Exact affinity wins; otherwise the first matching family prefix applies;
    otherwise the FE static fallback (empty here) is used.
    """
    exact = _CREDENTIAL_AFFINITY.get(worker_type)
    if exact is not None:
        return exact
    for prefix, kinds in _CREDENTIAL_AFFINITY_PREFIXES.items():
        if worker_type.startswith(prefix):
            return kinds
    return []


# Multi-node connector families → palette collection display name. One line per
# family (mirrors _CREDENTIAL_AFFINITY_PREFIXES). Standalone workers fall back to a
# title-cased worker_type. Collection is the SA-1734 browse axis for worker nodes.
_FAMILY_COLLECTION: dict[str, str] = {
    "jira_": "Jira",
    # req2tpl-worker: nine nodes mirroring the CLI's subcommands. Grouping is the
    # only thing the prefix does here — credential affinity is seeded per node
    # above, because most of these nodes take no credential at all.
    "req2tpl_": "req2tpl",
}


def _worker_collection(worker_type: str) -> str:
    for prefix, name in _FAMILY_COLLECTION.items():
        if worker_type.startswith(prefix):
            return name
    return worker_type.replace("_", " ").title() or "Workers"


def _ports_from_dict(ports: Any) -> NodePorts:
    if not isinstance(ports, dict):
        return NodePorts()
    return NodePorts(
        **{
            "in": [NodePort(id=p.get("id", ""), label=p.get("label", "")) for p in ports.get("in", [])],
            "out": [NodePort(id=p.get("id", ""), label=p.get("label", "")) for p in ports.get("out", [])],
        }
    )


def _capabilities_from_dicts(raw: Any) -> list[NodeCapability]:
    if not isinstance(raw, list):
        return []
    return [
        NodeCapability(domain=c.get("domain", ""), action=c.get("action", ""))
        for c in raw
        if isinstance(c, dict)
    ]


def _build_summary(worker: dict) -> NodeDefinitionSummary:
    worker_type = worker.get("worker_type", "")
    collection = _worker_collection(worker_type)
    # `worker` is an untyped dict off the wire. Two traps, both hit live:
    #   1. f"{NodeKind.ACTION}" renders "NodeKind.ACTION", not "action".
    #   2. NodeDefinitionSummary.kind is a pydantic NodeKind field, so an unknown value
    #      RAISES — while assembling the catalog. Blast radius is not one node: it is
    #      /api/v2/node-catalog 500ing and every tenant getting an empty palette because
    #      one worker registered kind="banana".
    # safe_kind_value closes both: always a plain str, never raises, unknown -> action
    # with a warning. The executor's registration doors DO reject junk — this is the READ
    # path, and a reader must be forgiving.
    kind = safe_kind_value(worker.get("kind"), context=f"worker_type={worker_type!r}")
    return NodeDefinitionSummary(
        id=WorkerRef(worker_type=worker_type).catalog_id,
        name=worker.get("name") or worker_type,
        description=worker.get("description", ""),
        provider="worker",
        node_type="TASK",
        category=f"{collection}.{kind}",
        kind=kind,
        collection=collection,
        icon=worker.get("icon") or "Cog",
        color=worker.get("color") or "#3B82F6",
        tags=worker.get("tags") or [],
        spec_version=worker.get("spec_version") or "1.0.0",
        visibility=NodeVisibility.VISIBLE,
        runtime=NodeRuntime.WORKER,
        ports=_ports_from_dict(worker.get("ports")),
    )


def _form_fields_strict(
    schema: Any,
    worker_type: str,
    kind: str,
    strict: bool,
) -> list[FormField]:
    if not isinstance(schema, list):
        return []
    if strict:
        return [FormField.model_validate(f) for f in schema]
    # Lenient mode: skip malformed entries with a warning.
    out: list[FormField] = []
    for f in schema:
        try:
            out.append(FormField.model_validate(f))
        except Exception as exc:
            logger.warning(
                "worker %r has malformed %s_schema field; skipping. err=%s",
                worker_type, kind, exc,
            )
    return out


def _build_detail(worker: dict, strict_validation: bool) -> NodeDefinitionDetail:
    summary = _build_summary(worker)
    worker_type = worker.get("worker_type", "")
    input_schema = _form_fields_strict(
        worker.get("input_schema"), worker_type, "input", strict_validation,
    )
    output_schema = _form_fields_strict(
        worker.get("output_schema"), worker_type, "output", strict_validation,
    )
    return NodeDefinitionDetail.model_validate({
        **summary.model_dump(by_alias=True),
        "inputSchema": [f.model_dump(by_alias=True) for f in input_schema],
        "outputSchema": [f.model_dump(by_alias=True) for f in output_schema],
        "capabilities": [
            {"domain": c.domain, "action": c.action}
            for c in _capabilities_from_dicts(worker.get("capabilities"))
        ],
        "workerType": worker_type,
        "supportedCredentialKinds": _credential_kinds_for(worker_type),
    })


class WorkerNodeProvider:
    """Projects executor-registered workers into catalog DTOs.

    Filters out workers with empty ``worker_type`` (defense in depth; Phase 0
    rejects them at registration), workers with a non-healthy status, workers
    whose ``last_heartbeat`` is older than ``stale_after_seconds`` (SA-2052 R5 —
    the belt-and-suspenders for the gap before R4's sweep; an absent heartbeat,
    e.g. the gRPC transport, is kept), and workers that declare no input/output
    port (the domain ``has_io_port`` rule — such a worker has no handle to wire
    into a graph, so it is not servable). Survivors are grouped by
    ``worker_type`` and projected to ONE summary per type (representative =
    freshest healthy replica), so N replicas of a type never render as N
    duplicate palette nodes.
    """

    def __init__(
        self,
        client: IWorkerRegistryClient,
        strict_schema_validation: bool = False,
        stale_after_seconds: int = 120,
    ) -> None:
        self._client = client
        # Phase 1 default False — malformed schemas from in-flight workers
        # should not break the whole catalog response. Phase 3 flips True.
        self._strict = strict_schema_validation
        # SA-2052 (R5): a registration whose last_heartbeat is older than this
        # window is dropped from the palette even if health_status still reads
        # healthy (the window between a worker going silent and R4's sweep marking
        # it). Absent last_heartbeat (gRPC transport) -> kept. Wired from
        # settings.WORKER_STALE_AFTER_SECONDS at bootstrap.py:876.
        self._stale_after = stale_after_seconds

    def list_summaries(self, tenant_id: str = "system") -> list[NodeDefinitionSummary]:
        # SA-2052 (R5): filter each row (empty worker_type, non-healthy status,
        # stale heartbeat, no I/O port), then GROUP survivors by worker_type and
        # emit ONE summary per type. The executor keys by (instance_id,
        # worker_type), so N replicas of a type arrive as N rows sharing
        # id="worker:<type>" (the FE's React key) — they MUST collapse to one node.
        survivors: dict[str, list[dict]] = {}
        for w in self._client.list_workers() or []:
            worker_type = w.get("worker_type", "")
            if not worker_type:
                logger.warning(
                    "catalog: worker with empty worker_type (id=%s) — skipping",
                    w.get("id"),
                )
                continue
            if w.get("health_status") not in _HEALTHY_STATUSES:
                continue
            if not is_fresh(w, self._stale_after):
                continue
            if not has_io_port(w.get("ports")):
                logger.warning(
                    "catalog: worker %r declares no input/output port — skipping",
                    worker_type,
                )
                continue
            survivors.setdefault(worker_type, []).append(w)
        out: list[NodeDefinitionSummary] = []
        for rows in survivors.values():
            # Representative = freshest healthy replica (newest last_heartbeat). On
            # a rolling deploy the newest heartbeat is the newer rollout, so its
            # spec represents the type (spec §5). Absent/blank -> datetime.min, so a
            # gRPC/never-heartbeat row only wins when it is the sole survivor.
            rep = max(rows, key=lambda r: parse_ts(r.get("last_heartbeat", "")))
            out.append(_build_summary(rep))
        return out

    def get_detail(
        self,
        node_id: str,
        spec_version: Optional[str] = None,
    ) -> Optional[NodeDefinitionDetail]:
        ref = WorkerRef.from_catalog_id(node_id)
        if ref is None:
            return None
        worker_type = ref.worker_type
        w = self._client.get_worker_by_type(worker_type)
        if not w:
            return None
        if not has_io_port(w.get("ports")):
            logger.warning(
                "catalog: worker %r declares no input/output port — not servable",
                worker_type,
            )
            return None
        registered = w.get("spec_version") or "1.0.0"
        if spec_version is not None and spec_version != registered:
            raise ValueError(
                f"worker {node_id!r}: pinned spec_version={spec_version!r} "
                f"does not match registered {registered!r}"
            )
        return _build_detail(w, self._strict)
