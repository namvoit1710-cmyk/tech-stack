"""ORM entity for Workflow."""
import json
from typing import Optional, Any
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text

from app.layer4_frameworks.orm.entities.base_audit_entity import BaseAuditORMEntity
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge, normalize_schema
from app.layer1_domain.value_objects.workflow_status import WorkflowStatus
from app.layer1_domain.value_objects.agent_ref import AgentRef


def _serialize_node(node: WorkflowNode) -> dict[str, Any]:
    """Serialize a WorkflowNode to a JSON-compatible dict.

    Handles AgentRef conversion to dicts and NodeType enum conversion.
    """
    data = dict(vars(node))
    # Convert AgentRef instances to dicts for JSON serialization
    if data.get("agents"):
        data["agents"] = [
            a.to_dict() if isinstance(a, AgentRef) else a
            for a in data["agents"]
        ]
    # Convert NodeType enum to string
    if hasattr(data.get("node_type"), "value"):
        data["node_type"] = data["node_type"].value
    return data


def _serialize_agents(agents: list[AgentRef]) -> list[dict[str, Any]]:
    """Serialize a list of AgentRef to JSON-compatible dicts."""
    return [
        a.to_dict() if isinstance(a, AgentRef) else a
        for a in agents
    ]


class WorkflowORMEntity(BaseAuditORMEntity):
    __tablename__ = "aiw_workflows"

    name: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(50), default="1")
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(50), default="DRAFT")
    input_schema: Mapped[str] = mapped_column(Text, default="[]")
    output_schema: Mapped[str] = mapped_column(Text, default="[]")
    nodes: Mapped[str] = mapped_column(Text, default="[]")
    edges: Mapped[str] = mapped_column(Text, default="[]")
    purpose: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    schema_version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    test_run_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, default=None)
    routing_path: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(Text, default="")
    instruction: Mapped[str] = mapped_column(Text, default="")
    agents_json: Mapped[str] = mapped_column(Text, default="[]")

    def to_domain(self) -> Workflow:
        nodes_data = json.loads(self.nodes) if self.nodes else []
        edges_data = json.loads(self.edges) if self.edges else []
        agents_data = json.loads(self.agents_json) if self.agents_json else []
        return Workflow(
            id=self.id,
            name=self.name,
            version=self.version,
            description=self.description,
            status=WorkflowStatus(self.status),
            input_schema=normalize_schema(json.loads(self.input_schema) if self.input_schema else None),
            output_schema=normalize_schema(json.loads(self.output_schema) if self.output_schema else None),
            nodes=[WorkflowNode(**n) for n in nodes_data],
            edges=[WorkflowEdge(**e) for e in edges_data],
            purpose=self.purpose or "",
            metadata=json.loads(self.metadata_json) if self.metadata_json else {},
            schema_version=self.schema_version or "1.0.0",
            created_at=self.created_at or "",
            updated_at=self.updated_at or "",
            test_run_id=self.test_run_id,
            action=self.action or "",
            instruction=self.instruction or "",
            agents=agents_data,
        )

    @classmethod
    def from_domain(cls, entity: Workflow) -> "WorkflowORMEntity":
        return cls(
            id=entity.id,
            name=entity.name,
            version=entity.version,
            description=entity.description,
            status=entity.status.value if hasattr(entity.status, "value") else str(entity.status),
            input_schema=json.dumps(entity.input_schema),
            output_schema=json.dumps(entity.output_schema),
            nodes=json.dumps([_serialize_node(n) for n in entity.nodes]),
            edges=json.dumps([vars(e) if hasattr(e, "__dict__") else e for e in entity.edges]),
            purpose=entity.purpose,
            metadata_json=json.dumps(entity.metadata),
            schema_version=entity.schema_version,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            test_run_id=entity.test_run_id,
            action=entity.action,
            instruction=entity.instruction,
            agents_json=json.dumps(_serialize_agents(entity.agents)),
        )
