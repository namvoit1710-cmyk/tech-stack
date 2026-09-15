"""SDK register worker use case."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.message_broker_interface import IMessageBrokerClient
from app.layer1_domain.interfaces.worker_registry_store_interface import IWorkerRegistryStore
from app.layer1_domain.entities.worker_registration import WorkerRegistration, WorkerCapability, WorkerFunctionDef
from app.layer1_domain.value_objects.node_kind import NodeKind, normalize_kind
from app.layer1_domain.value_objects.delivery_mode import DeliveryMode, normalize_delivery_mode


# Shared topic name for CP catalog-cache invalidation.
CATALOG_CHANGED_TOPIC = "catalog.changed"


@dataclass
class SdkRegisterWorkerInput:
    worker_type: str = ""
    version: str = ""
    spec_version: str = "1.0.0"
    # instance_id is the worker PROCESS; `id`/worker_id is this REGISTRATION (the
    # instance_id x worker_type pair). Blank = caller named no process ->
    # WorkerRegistration.__post_init__ derives the registration id itself; this
    # use case mints no identity.
    instance_id: str = ""
    # R14: the deployable this registration belongs to. Carried through
    # untouched (no normalization, no derivation) — same discipline as host/
    # pid/started_at above: this dataclass is a plain pass-through of what the
    # DTO decoded off the wire. Derivation from the endpoint host happens
    # exactly once, in WorkerRegistration.__post_init__, so a blank value here
    # is not resolved twice by two different pieces of code that could drift.
    worker_app: str = ""
    host: str = ""
    pid: int = 0
    started_at: str = ""
    # Raw pre-normalization wire value (may be blank or an invented kind from a
    # rogue caller) — genuinely a plain str, not NodeKind, since it hasn't been
    # through normalize_kind() yet (that happens below, building the entity).
    # The default itself is derived from the enum so it can't drift from it.
    kind: str = NodeKind.ACTION.value
    # SA-2026: how the executor delivers tasks — "push" (default) or "pull".
    # Raw pre-normalization wire value; normalize_delivery_mode() runs below.
    delivery_mode: str = DeliveryMode.PUSH.value
    endpoint: str = ""
    sdk_version: str = ""
    input_schema: Optional[list] = None
    output_schema: Optional[list] = None
    name: str = ""
    description: str = ""
    node_class: str = "BUSINESS"
    icon: str = "Cog"
    color: str = "#3B82F6"
    tags: list[str] = field(default_factory=list)
    capabilities: list[dict[str, str]] = field(default_factory=list)
    ports: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {"in": [], "out": []})
    functions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SdkRegisterWorkerOutput:
    # `worker_id` is the REGISTRATION id — the (process x worker_type) pair. Its
    # name and position are unchanged: it is the value every existing SDK reads off
    # this response and replays on heartbeat and deregister.
    # `instance_id` is the PROCESS half of that pair, reported separately because
    # the two stopped being the same string when a caller names its process
    # (SA-2048). They coincide only when no instance_id was supplied — which is
    # every caller today.
    worker_id: str = ""
    instance_id: str = ""
    worker_type: str = ""
    version: str = ""
    endpoint_url: str = ""
    health_status: str = ""


class SdkRegisterWorkerUseCase:
    """Registers a worker using the SDK registration payload."""

    def __init__(
        self,
        logger: ILogger,
        registry_store: IWorkerRegistryStore,
        message_broker_client: Optional[IMessageBrokerClient] = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.registry_store = registry_store
        self.message_broker_client = message_broker_client

    def execute(self, input: SdkRegisterWorkerInput) -> dict:
        if not input.worker_type or not input.worker_type.strip():
            raise ValueError("worker_type is required for SDK registration")

        self.logger.info(
            "SDK worker registration",
            worker_type=input.worker_type,
            version=input.version,
        )

        # Build capabilities from input or default to worker_type:execute
        if input.capabilities:
            caps = [WorkerCapability(domain=c.get("domain", ""), action=c.get("action", "")) for c in input.capabilities]
        else:
            caps = [WorkerCapability(domain=input.worker_type, action="execute")]

        func_defs = [
            WorkerFunctionDef(
                name=f.get("name", ""),
                description=f.get("description", ""),
                input_schema=f.get("input_schema", []),
                output_schema=f.get("output_schema", []),
            )
            for f in (input.functions or [])
        ]

        worker = WorkerRegistration(
            # SA-2048: this method deliberately mints NO identity. The entity derives
            # the registration id from the (instance_id, worker_type) pair — see
            # WorkerRegistration.__post_init__. Synthesizing an instance_id here
            # would pin every registration to a process the caller never claimed and
            # make the entity's "no process named" branch unreachable.
            # The only thing that happens here is whitespace-stripping: the same
            # raw-wire-value discipline this method already applies to `kind` and
            # `delivery_mode` below. "   " means the caller did not say, same as "".
            instance_id=(input.instance_id or "").strip(),
            # R14: pass the raw wire value straight through, blank or not. The
            # entity's __post_init__ is the ONLY place that resolves a blank
            # into the derived-from-endpoint value (worker_app_from_endpoint) —
            # this use case, like it does for kind/delivery_mode's *raw* value
            # here, does not pre-empt that decision.
            worker_app=input.worker_app,
            host=input.host,
            pid=input.pid,
            started_at=input.started_at or datetime.now(timezone.utc).isoformat(),
            name=input.name or input.worker_type,
            capabilities=caps,
            endpoint_url=input.endpoint,
            worker_type=input.worker_type,
            version=input.version,
            spec_version=input.spec_version or "1.0.0",
            # Same enum as the HTTP DTO and the gRPC servicer (SA-1734 blocker fix):
            # blank normalizes to NodeKind.ACTION; a non-empty unknown value raises.
            kind=normalize_kind(input.kind),
            # Blank normalizes to PUSH (proto3-additive back-compat); an unknown
            # value raises ValueError -> 422/INVALID_ARGUMENT at the boundary.
            delivery_mode=normalize_delivery_mode(input.delivery_mode),
            sdk_version=input.sdk_version,
            input_schema=input.input_schema,
            output_schema=input.output_schema,
            description=input.description,
            node_class=input.node_class,
            icon=input.icon,
            color=input.color,
            tags=input.tags,
            ports=input.ports,
            functions=func_defs,
        )

        self.registry_store.register(worker)
        self._emit_catalog_changed(worker)

        return SdkRegisterWorkerOutput(
            worker_id=worker.id,
            instance_id=worker.instance_id,
            worker_type=worker.worker_type,
            version=worker.version,
            endpoint_url=worker.endpoint_url,
            health_status=worker.health_status,
        ).__dict__

    def _emit_catalog_changed(self, worker: WorkerRegistration) -> None:
        """Notify CP that a new/updated worker affects the palette.

        Emits only when a broker is wired. Failures are logged, not raised —
        a broken broker must not block registration. CP falls back to TTL.
        """
        if self.message_broker_client is None:
            return
        try:
            self.message_broker_client.publish_to_topic(
                CATALOG_CHANGED_TOPIC,
                {
                    "action": "registered",
                    "worker_type": worker.worker_type,
                    "spec_version": worker.spec_version,
                    "tenant_id": "system",
                },
            )
        except Exception as exc:
            self.logger.warning(
                "catalog.changed publish failed (register)",
                worker_type=worker.worker_type,
                error=str(exc),
            )
