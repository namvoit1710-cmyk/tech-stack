from typing import Any, Dict, Optional

from pydantic import Field

from .base_dto import BaseInputDto, BaseOutputDto


class ExecuteInputDto(BaseInputDto):
    """Request body for POST /api/v1/execute (was ``ExecuteRequest``)."""

    message: str
    conv_id: str = ""
    user_id: str = "anonymous"
    tenant_id: str = "default"
    source: str = "api"
    parameters: Dict[str, Any] = Field(default_factory=dict)


class ExecuteOutputDto(BaseOutputDto):
    """Typed mirror of the ``formatted_response`` wire shape (C5).

    ``transport_state`` is optional because the error/not-support responses omit
    it — dumping with ``exclude_none`` reproduces the exact shape. The live route
    returns the graph's dict verbatim (byte-identical); this DTO documents and
    validates that contract (see the round-trip test).
    """

    content: str = ""
    status: str = ""
    agent_type: str = ""
    agent_data: Dict[str, Any] = Field(default_factory=dict)
    transport_state: Optional[str] = None
