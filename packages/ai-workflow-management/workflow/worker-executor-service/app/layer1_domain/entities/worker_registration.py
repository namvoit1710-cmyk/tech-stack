"""Worker registration and capability entities."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from app.layer1_domain.value_objects.node_kind import NodeKind, normalize_kind
from app.layer1_domain.value_objects.delivery_mode import DeliveryMode, normalize_delivery_mode
from app.layer1_domain.value_objects.worker_app import worker_app_from_endpoint


@dataclass
class WorkerCapability:
    """Represents a single capability that a worker can perform."""
    domain: str = ""
    action: str = ""


@dataclass
class WorkerFunctionDef:
    """Metadata for a named function exposed by a worker."""
    name: str = ""
    description: str = ""
    input_schema: list[dict[str, Any]] = field(default_factory=list)
    output_schema: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class WorkerRegistration:
    """Represents a registered worker in the system."""
    # The REGISTRATION: this worker_type, offered by that process. Derived from the
    # (instance_id, worker_type) pair in __post_init__, so setting it directly is
    # honoured ONLY when no instance_id is supplied. The wire calls it `worker_id`.
    # No caller supplies an instance_id today, so no caller reaches the
    # derived-pair branch in __post_init__.
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    # --- R1 (SA-2048): per-instance identity ---
    # The worker PROCESS this registration came from -- one CF app instance.
    # Blank = the caller did not say, and the executor treats this registration as
    # its own instance (see __post_init__).
    # SA-2055 (R8) will have the SDK send `<app_name>_${CF_INSTANCE_INDEX}`. CF's
    # instance INDEX is stable across crash-restart -- Diego keys an ActualLRP by
    # {process_guid, index} and a crash clears only the ActualLRPInstanceKey -- so
    # a restarted replica reuses its own rows instead of orphaning them.
    # Deliberately NOT ${CF_INSTANCE_GUID}: that is regenerated on every restart
    # and would leak one row per restart, unbounded.
    instance_id: str = ""
    # --- R14: the deployable that owns this registration ---
    # SDK-sent (CF application_name). Blank -> derived from endpoint_url in
    # __post_init__. worker_type is a node type and instance_id is a process;
    # neither can answer "how many workers", which is why this exists.
    worker_app: str = ""
    # True when NO instance_id was supplied and the uuid4 default became the
    # instance. A replica count over synthesized ids is fiction (one process
    # registering 22 node types would read as 22 replicas), so the read paths
    # report identity as "pending" instead of a number.
    instance_synthesized: bool = False
    host: str = ""
    # One CF container runs one app process, so pid is near-constant there; it only
    # disambiguates two processes sharing a host (local dev, future k8s multi-process).
    pid: int = 0
    started_at: str = ""
    name: str = ""
    capabilities: list[WorkerCapability] = field(default_factory=list)
    endpoint_url: str = ""
    health_status: str = "healthy"
    last_heartbeat: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ttl_seconds: int = 60
    worker_type: str = ""
    # Build identity — bumps on code changes.
    version: str = ""
    # Contract identity — bumps when input/output/ports change.
    spec_version: str = "1.0.0"
    # Functional kind of the connector node. Vocabulary is NodeKind:
    # trigger/action/read/logic/human/transform/util.
    kind: NodeKind = NodeKind.ACTION
    # How the executor delivers tasks to this worker (SA-2026):
    #   push (default) — executor POSTs /execute-async + callback (legacy path)
    #   pull           — executor stores the task; the worker leases it
    # Blank/absent normalizes to PUSH so every pre-SA-2026 worker is unchanged.
    delivery_mode: DeliveryMode = DeliveryMode.PUSH
    sdk_version: str = ""
    input_schema: Optional[list] = None
    output_schema: Optional[list] = None
    description: str = ""
    node_class: str = "BUSINESS"
    icon: str = "Cog"
    color: str = "#3B82F6"
    tags: list[str] = field(default_factory=list)
    ports: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {"in": [], "out": []})
    functions: list[WorkerFunctionDef] = field(default_factory=list)

    def __post_init__(self) -> None:
        # The executor is the network boundary — validate `kind` here even
        # when a caller bypasses the HTTP DTO / gRPC servicer validation
        # (e.g. constructs this entity directly). Raises ValueError for a
        # non-empty unknown kind; blank normalizes to NodeKind.ACTION.
        self.kind = normalize_kind(self.kind)
        # Same boundary discipline for delivery_mode (SA-2026): blank -> PUSH,
        # a non-empty unknown value (e.g. "poll") raises ValueError.
        self.delivery_mode = normalize_delivery_mode(self.delivery_mode)
        # SA-2048: a registration is the PAIR — this worker_type, offered by that
        # process — so `id` is derived from both halves and is never either one
        # alone. Keying on the process alone loses registrations: one process
        # registers many worker_types (jira-worker registers 22), and only the last
        # would survive. Keying on worker_type alone loses replicas: worker_type is a
        # CLASS, and every replica of it would collapse onto one shared row.
        #
        # When no process is named, the registration IS its own instance: the uuid4
        # default stands and instance_id adopts it. That yields one row per register
        # call — the honest count while the SDK sends no instance_id, and the reason
        # this change is inert until SA-2055 sends a real one.
        #
        # Derived here, next to the normalize_* calls, for the same reason they live
        # here: a caller that builds this entity directly — a test, the static
        # registration in bootstrap, a future adapter — cannot produce an
        # inconsistent pair.
        #
        # HONEST LIMIT: this holds at CONSTRUCTION. A caller that reassigns .id,
        # .instance_id, or .worker_type afterwards desyncs the pair and nothing
        # here can stop it. The HANA store's register() does exactly that on
        # upsert (worker.id = existing.id) -- though that store has zero
        # production callers today; bootstrap wires MemoryWorkerRegistryStore
        # instead. This is not a runtime guarantee. Do not write one.
        #
        # Captured BEFORE the fallback below, which erases the evidence by
        # copying id into instance_id.
        self.instance_synthesized = not self.instance_id
        if self.instance_id:
            self.id = f"{self.instance_id}:{self.worker_type}"
        else:
            self.instance_id = self.id
        if not self.worker_app:
            self.worker_app = worker_app_from_endpoint(self.endpoint_url)
