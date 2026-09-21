from dataclasses import dataclass, field
from typing import Any, Optional

from app.layer2_application.features.orchestrate_graph.use_cases.dtos.wf_info import WfInfo


@dataclass
class OrchestrationParameters:
    """Typed view of `OrchestrationGraphCommand.parameters` (previously a raw dict).

    Parsed once at the edge via `from_dict`, so the use case works with attributes
    (`parameters.wf_info`, `parameters.uploaded_file_ids`) instead of `dict.get(...)`.
    """

    wf_info: Optional[WfInfo] = None
    uploaded_file_ids: list[str] = field(default_factory=list)
    # SA-2114: direct-to-agent routing (type=AGENT + agent_id).
    agent_id: str = ""
    type: Optional[str] = None
    # SA-1436: tenant isolation for long-term memory recall/save.
    tenant_id: str = ""

    @classmethod
    def from_dict(cls, parameters: Any) -> "OrchestrationParameters":
        """Build from a raw parameters dict (HTTP payload or an asdict round-trip
        off the queue). Unknown/absent keys degrade gracefully to defaults."""
        if isinstance(parameters, cls):
            return parameters
        if not isinstance(parameters, dict):
            return cls()

        raw_ids = parameters.get("uploaded_file_ids") or []
        uploaded_file_ids = (
            [str(file_id) for file_id in raw_ids if file_id not in (None, "")]
            if isinstance(raw_ids, list)
            else []
        )
        return cls(
            wf_info=WfInfo.from_parameters(parameters),
            uploaded_file_ids=uploaded_file_ids,
            agent_id=str(parameters.get("agent_id") or "").strip(),
            type=parameters.get("type"),
            tenant_id=str(parameters.get("tenant_id") or "").strip(),
        )
