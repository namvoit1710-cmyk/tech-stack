"""ORM entity for WorkflowRun."""
import json
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text, Integer
from typing import Optional

from app.layer4_frameworks.orm.entities.base_entity import BaseORMEntity
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.entities.workflow import WorkflowEdge
from app.layer1_domain.value_objects.run_status import RunStatus


class WorkflowRunORMEntity(BaseORMEntity):
    __tablename__ = "aiw_workflow_runs"

    workflow_id: Mapped[str] = mapped_column(String(36))
    workflow_version: Mapped[str] = mapped_column(String(50), default="1")
    status: Mapped[str] = mapped_column(String(50), default="PENDING")
    input_data: Mapped[str] = mapped_column(Text, default="{}")
    output_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    context: Mapped[str] = mapped_column(Text, default="{}")
    parent_run_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    parent_node_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    depth_level: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    completed_at: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(100), default="system")
    correlation_id: Mapped[str] = mapped_column(String(100), default="")
    callback_topic: Mapped[str] = mapped_column(String(255), default="")
    is_test: Mapped[int] = mapped_column(Integer, default=0)
    test_mode: Mapped[str] = mapped_column(String(20), default="")
    # Free-form caller metadata, JSON-encoded. Echoed onto outbound messages.
    metadata_json: Mapped[str] = mapped_column("metadata", Text, default="{}")

    @staticmethod
    def _rebuild_topology(topology_data: dict) -> GraphTopology:
        edge_map_raw = topology_data.get("edge_map", {})
        edge_map: dict[str, list[WorkflowEdge]] = {}
        for node_id, edges in edge_map_raw.items():
            edge_map[node_id] = [
                WorkflowEdge(
                    id=e.get("id", ""),
                    source_node_id=e.get("source_node_id", ""),
                    target_node_id=e.get("target_node_id", ""),
                    condition=e.get("condition"),
                    source_port_id=e.get("source_port_id", ""),
                    target_port_id=e.get("target_port_id", ""),
                    label=e.get("label", ""),
                    description=e.get("description", ""),
                )
                for e in edges
            ]
        return GraphTopology(
            adjacency=topology_data.get("adjacency", {}),
            predecessors=topology_data.get("predecessors", {}),
            edge_map=edge_map,
            entry_nodes=topology_data.get("entry_nodes", []),
            leaf_nodes=topology_data.get("leaf_nodes", []),
        )

    def to_domain(self) -> WorkflowRun:
        ctx_data = json.loads(self.context) if self.context else {}
        topology_data = ctx_data.pop("topology", {})
        # Convert sets from lists
        completed = set(ctx_data.pop("completed_nodes", []))
        skipped = set(ctx_data.pop("skipped_nodes", []))
        context = RunContext(
            variables=ctx_data.get("variables", {}),
            loop_state=ctx_data.get("loop_state", {}),
            completed_nodes=completed,
            current_node=ctx_data.get("current_node", ""),
            skipped_nodes=skipped,
            node_visits=dict(ctx_data.get("node_visits", {}) or {}),
            child_runs=ctx_data.get("child_runs", {}),
            topology=self._rebuild_topology(topology_data) if topology_data else GraphTopology(),
        )
        return WorkflowRun(
            id=self.id,
            workflow_id=self.workflow_id,
            workflow_version=self.workflow_version,
            status=RunStatus(self.status),
            input_data=json.loads(self.input_data) if self.input_data else {},
            output_data=json.loads(self.output_data) if self.output_data else None,
            context=context,
            parent_run_id=self.parent_run_id,
            parent_node_id=self.parent_node_id,
            depth_level=self.depth_level,
            error=self.error,
            started_at=self.started_at or "",
            completed_at=self.completed_at,
            tenant_id=self.tenant_id,
            correlation_id=self.correlation_id,
            callback_topic=self.callback_topic,
            is_test=bool(self.is_test),
            test_mode=self.test_mode or "",
            metadata=json.loads(self.metadata_json) if self.metadata_json else {},
        )

    @staticmethod
    def _edge_to_dict(edge: WorkflowEdge) -> dict:
        return {
            "id": edge.id,
            "source_node_id": edge.source_node_id,
            "target_node_id": edge.target_node_id,
            "condition": edge.condition,
            "source_port_id": edge.source_port_id,
            "target_port_id": edge.target_port_id,
            "label": edge.label,
            "description": edge.description,
        }

    @classmethod
    def from_domain(cls, entity: WorkflowRun) -> "WorkflowRunORMEntity":
        ctx = entity.context
        dag = ctx.topology
        topology_dict = {
            "adjacency": dag.adjacency,
            "predecessors": dag.predecessors,
            "edge_map": {
                node_id: [cls._edge_to_dict(e) for e in edges]
                for node_id, edges in dag.edge_map.items()
            },
            "entry_nodes": dag.entry_nodes,
            "leaf_nodes": dag.leaf_nodes,
        } if dag else {}
        ctx_dict = {
            "variables": ctx.variables,
            "loop_state": ctx.loop_state,
            "completed_nodes": list(ctx.completed_nodes),
            "current_node": ctx.current_node,
            "skipped_nodes": list(ctx.skipped_nodes),
            "node_visits": dict(ctx.node_visits),
            "child_runs": ctx.child_runs,
            "topology": topology_dict,
        }
        return cls(
            id=entity.id,
            workflow_id=entity.workflow_id,
            workflow_version=entity.workflow_version,
            status=entity.status.value if hasattr(entity.status, "value") else str(entity.status),
            input_data=json.dumps(entity.input_data),
            output_data=json.dumps(entity.output_data) if entity.output_data else None,
            context=json.dumps(ctx_dict),
            parent_run_id=entity.parent_run_id,
            parent_node_id=entity.parent_node_id,
            depth_level=entity.depth_level,
            error=entity.error,
            started_at=entity.started_at,
            completed_at=entity.completed_at,
            tenant_id=entity.tenant_id,
            correlation_id=entity.correlation_id,
            callback_topic=entity.callback_topic,
            metadata_json=json.dumps(entity.metadata or {}),
        )
