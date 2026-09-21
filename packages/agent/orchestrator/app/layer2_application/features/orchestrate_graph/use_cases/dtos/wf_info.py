from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class WfInfo:
    """A specific workflow named in an /async request (`parameters.wf_info`).

    Created only through `from_parameters`, which enforces the domain constraint
    that a usable workflow reference must carry a non-empty string `id`.
    """

    id: str
    name: str = ""
    description: str = ""

    @classmethod
    def from_parameters(cls, parameters: Any) -> Optional["WfInfo"]:
        """Parse `parameters.wf_info` into a `WfInfo`.

        Returns `None` when the request does not name a workflow, i.e. when
        `parameters` is not a dict, `wf_info` is absent/malformed, or `id` is
        missing or not a non-empty string.
        """
        if not isinstance(parameters, dict):
            return None

        raw = parameters.get("wf_info")
        if not isinstance(raw, dict):
            return None

        workflow_id = raw.get("id")
        if not isinstance(workflow_id, str) or not workflow_id:
            return None

        return cls(
            id=workflow_id,
            name=raw.get("name") or "",
            description=raw.get("description") or "",
        )
