from dataclasses import dataclass
from typing import Optional


@dataclass
class DispatchTaskCommand:
    """Input for OrchestrateGraphUseCase._dispatch_task.

    A single parameter object so new dispatch fields can be added over time
    without changing the method signature or its callers.
    """

    conv_id: str
    raw_user_message: str = ""
    workflow_id: Optional[str] = None
    # SA-1436: recalled long-term memory + any base metadata to merge onto the
    # QueueEvent so the business agent receives memory as non-authoritative context.
    memory_matches: Optional[list] = None
    base_metadata: Optional[dict] = None
