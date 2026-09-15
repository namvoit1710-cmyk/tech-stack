from dataclasses import dataclass, field
from typing import Any, Optional

from worker_sdk.layer1_domain.entities.base_entity import BaseDomainEntity
from worker_sdk.layer1_domain.entities.worker_function import WorkerFunctionDefinition
from worker_sdk.layer1_domain.value_objects.node_kind import NodeKind
from worker_sdk.layer1_domain.value_objects.port import default_task_ports


@dataclass(kw_only=True)
class WorkerRegistration(BaseDomainEntity):
    worker_type: str
    # Build identity — bumps on code changes.
    version: str
    # Contract identity — bumps when input/output/ports change.
    spec_version: str = "1.0.0"
    endpoint: str
    sdk_version: Optional[str] = None
    input_schema: Optional[dict[str, Any]] = None
    output_schema: Optional[dict[str, Any]] = None
    name: str = ""
    description: str = ""
    node_class: str = "BUSINESS"
    kind: NodeKind = NodeKind.ACTION
    # How the executor delivers tasks to this worker (SA-2026): "push" (default,
    # executor POSTs /execute-async) or "pull" (executor stores the task; this
    # worker leases it — APP_MODE=PULL). The executor validates the value at
    # registration; the SDK sends it verbatim.
    delivery_mode: str = "push"
    icon: str = "Cog"
    color: str = "#3B82F6"
    tags: list[str] = field(default_factory=list)
    capabilities: list[dict[str, str]] = field(default_factory=list)
    ports: dict[str, list[dict[str, Any]]] = field(default_factory=default_task_ports)
    functions: list[WorkerFunctionDefinition] = field(default_factory=list)

    # --- R8 (SA-2055): process identity — "which process am I?" ---
    # Blank/0 = "ask the process": HttpWorkerRegistry.register() fills these from
    # layer4_frameworks/config/instance_identity at send time, so the 5 construction
    # sites across SERVER/HEADLESS/PULL need no edit. They are settable so an
    # embedder (or a test) can pin them.
    #
    # NOT a registration id. The executor derives that (id = instance_id:worker_type,
    # keystone §4) — the boundary owns uniqueness. A blank instance_id is CORRECT,
    # not a stopgap: the executor then treats each registration as its own instance
    # (uuid4 per call), which is the best available truth off CF (keystone §4.2).
    instance_id: str = ""
    host: str = ""
    pid: int = 0
    started_at: str = ""
    # R14 (SA-2047): the CF application_name, e.g. "jira-worker" — the same
    # value across every replica of this deployable (unlike instance_id, which
    # is per-replica). Blank/"" = "ask the process": HttpWorkerRegistry.register()
    # fills it from instance_identity.worker_app() at send time. Blank off CF
    # is correct, not a stopgap: the executor then derives a value from the
    # endpoint host instead (degraded-but-working, never a crash).
    worker_app: str = ""

    def __post_init__(self) -> None:
        try:
            self.kind = NodeKind(self.kind)
        except ValueError:
            raise ValueError(
                f"WorkerRegistration.kind {self.kind!r} must be one of "
                f"{tuple(k.value for k in NodeKind)}"
            ) from None
