"""OUTPUT node handler: defines output schema for workflow results."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer1_domain.node_spec.specs import OutputNodeConfig
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer2_application.node_handlers.inline_bases import (
    AsyncInlineHandlerBase,
)
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import VariableResolver, VariableResolutionError
from app.layer1_domain.exceptions import ExpressionError

logger = logging.getLogger(__name__)


def _parse_output_schema(raw: Any) -> list:
    """Parse output_schema from JSON string or list.
    
    Supports both legacy JSON schema and new form-based array format:
    [{"key": "fieldName", "type": "string", "value": "{{$nodeId.output}}"}]
    """
    if isinstance(raw, (list, tuple)):
        return list(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def _build_type_map_from_schema(schema: list) -> dict[str, str]:
    """Extract field key → type mapping from output_schema.
    
    Handles form-based schema: [{"key": "name", "type": "string", "value": "..."}]
    """
    type_map = {}
    for field in schema or []:
        if isinstance(field, dict):
            key = field.get("key")
            field_type = field.get("type", "string")
            if key:
                type_map[key] = field_type
    return type_map


def _build_input_binding_from_schema(schema: list) -> dict[str, str]:
    """Build input_binding dict from output_schema.
    
    Converts form-based schema to input_binding format:
    [{"key": "name", "type": "string", "value": "{{$nodeId.output}}"}]
    → {"name": "{{$nodeId.output}}"}
    """
    binding = {}
    for field in schema or []:
        if isinstance(field, dict):
            key = field.get("key")
            value = field.get("value", "")
            # Only schema fields that carry an actual value expression
            # contribute to the binding. A valueless field (key/type/label
            # only) must NOT inject an empty `""` entry — that produced
            # spurious empty outputs (which then failed number/object/array
            # coercion) and, worse, masked the node-level ``input_binding``
            # fallback used by OUTPUT nodes whose bindings live on the node.
            if key and value not in (None, ""):
                binding[key] = value
    return binding


class OutputNodeHandler(AsyncInlineHandlerBase):
    """Handler for OUTPUT node type.

    At design-time: Allows user to define output_schema for workflow outputs.
    At runtime: Resolves input_binding values according to output_schema and
    passes them as node output for downstream nodes or workflow result.
    """

    def __init__(
        self,
        variable_resolver: VariableResolver,
        data_store_service: Any = None,
    ) -> None:
        self._resolver = variable_resolver
        self._data_store = data_store_service
    
    async def handle(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        workflow: Workflow,
        topology: GraphTopology,
        completed_task: Task | None,
        task_factory: TaskFactory,
    ) -> NodeHandlerResult:
        """Execute the OUTPUT node.
        
        Resolves values from output_schema (form-based) and produces
        node output that can be used as workflow result.
        
        output_schema format: [{"key": "name", "type": "string", "value": "{{$nodeId.output}}"}]
        """
        result = NodeHandlerResult()
        result.completion_mode = "immediate"
        
        # Get output_schema from node config
        output_schema = _parse_output_schema(
            OutputNodeConfig.from_node(node).output_schema,
        )
        
        # Build type_map from output_schema for proper type coercion
        type_map = _build_type_map_from_schema(output_schema)
        
        # Build input_binding from output_schema (form-based)
        # Fallback to node.input_binding if output_schema doesn't have values
        input_binding = _build_input_binding_from_schema(output_schema)
        if not input_binding and node.input_binding:
            input_binding = node.input_binding
        
        # Resolve input_binding to get output values
        output_data: dict[str, Any] = {}
        if input_binding:
            try:
                output_data = await self._resolver.resolve_mapping(
                    input_binding,
                    run.context.variables,
                    type_map=type_map,
                )
            except VariableResolutionError as exc:
                raise ExpressionError(
                    f"Node '{node.id}': output resolution failed: {exc}"
                ) from exc
        
        # Key the resolved fields under the node id (universal convention —
        # ``apply_state_updates_as_files`` writes keys verbatim, so flat
        # field keys would land as top-level run variables instead of a
        # ``$<node>.output.<field>``-addressable node entry, and the node
        # would be invisible to ``_build_output_data`` / the sub-workflow
        # OUTPUT contract).
        result.state_updates = {
            node.id: {
                "output": output_data,
                "metadata": {"type": "output"},
            },
        }
        
        # Mark task as completed
        result.new_task_status = "COMPLETED"
        
        logger.debug(
            "OUTPUT node %s produced output: %s",
            node.id,
            list(output_data.keys()),
        )
        
        return result
