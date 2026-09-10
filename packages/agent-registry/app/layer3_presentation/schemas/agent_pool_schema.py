"""Agent pool API schemas (SA RBAC v1, Task 4)."""

from pydantic import BaseModel, Field

from app.layer2_application.dtos.rbac_read_models import AgentPoolDetail, AgentPoolWithLinks


class AgentPoolResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    status: str
    created_by: str | None = None

    @classmethod
    def from_detail(cls, d: AgentPoolDetail) -> "AgentPoolResponse":
        return cls(id=d.id, name=d.name, description=d.description, status=d.status, created_by=d.created_by)


class AgentPoolDetailResponse(AgentPoolResponse):
    agents: list[str] = []
    members: list[str] = []

    @classmethod
    def from_view(cls, v: AgentPoolWithLinks) -> "AgentPoolDetailResponse":
        return cls(
            id=v.pool.id, name=v.pool.name, description=v.pool.description, status=v.pool.status,
            created_by=v.pool.created_by, agents=list(v.agent_ids), members=list(v.member_ids),
        )


class AgentPoolListResponse(BaseModel):
    pools: list[AgentPoolResponse]
    total: int


class CreatePoolRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=500)


class UpdatePoolRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=500)


class AddAgentRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=36)


class AddMemberRequest(BaseModel):
    external_id: str = Field(min_length=1, max_length=255)
