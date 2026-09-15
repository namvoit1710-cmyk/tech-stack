"""TEMPLATE_DATA_EDIT handler — governance/search-help-backed data editor.

Reuses the entire DATA_EDIT pause/edit/submit machinery (file provisioning,
PATCH /data, working-ref output). The only differences:

* the table schema is generated from a governance *template* and Integration
  Hub search-help endpoints (not from upstream artifact columns), and
* the rich schema is served verbatim on ``GET /schema`` via
  :class:`StaticSchemaProvider` (DATA_EDIT's view-driven provider would
  rebuild plain text columns and drop the value-help dropdowns).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.template_schema import build_template_data_edit_schema
from app.layer2_application.node_handlers.data_edit_handler import DataEditNodeHandler
from app.layer2_application.node_handlers.schema_providers import StaticSchemaProvider
from app.layer2_application.resolve.variable_resolver import VariableResolver

logger = logging.getLogger(__name__)


class TemplateDataEditNodeHandler(DataEditNodeHandler):
    """DATA_EDIT variant whose table is driven by a governance template."""

    pending_kind: ClassVar[str] = "data_edit"

    def __init__(
        self,
        variable_resolver: VariableResolver,
        file_service_client: Any = None,
        data_store_service: Any = None,
        governance_client: Any = None,
        integration_hub_client: Any = None,
    ) -> None:
        super().__init__(variable_resolver, file_service_client, data_store_service)
        self._governance = governance_client
        self._hub = integration_hub_client
        # Serve the stamped (value-help) schema verbatim — do NOT rebuild from
        # working-ref columns the way DATA_EDIT does.
        self._schema_provider = StaticSchemaProvider(variable_resolver)

    async def resolve_prefill(
        self,
        *,
        node: WorkflowNode,
        run: WorkflowRun,
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        resolved = await super().resolve_prefill(node=node, run=run, ctx=ctx)
        # The requester file seeds the table — reuse DATA_EDIT's file_id plumbing.
        if resolved.get("requester_file_id") and not resolved.get("file_id"):
            resolved["file_id"] = resolved["requester_file_id"]
        return resolved

    async def build_input_schema(
        self,
        *,
        node: WorkflowNode,
        run: WorkflowRun,
        workflow: Workflow,
        prefilled: dict[str, Any],
        ctx: dict[str, Any],
    ) -> list:
        template_id = str(prefilled.get("template_id") or "")
        model_id = str(prefilled.get("business_data_model_id") or "")
        file_id = str(prefilled.get("file_id") or prefilled.get("requester_file_id") or "")
        readonly = bool(prefilled.get("readonly", False))
        instruction = prefilled.get("instruction", node.instruction or "")
        try:
            page_size = int(prefilled.get("page_size") or 100)
        except (TypeError, ValueError):
            page_size = 100

        fields = []
        if self._governance is not None and template_id:
            fields = await self._governance.get_template_fields(template_id)
        elif not template_id:
            logger.warning("Node %s: no template_id resolved", node.id)

        # Value-help columns resolve their rows through the registered FE
        # ``integration-hub`` service (which owns the base URL); the schema
        # only carries the relative search-help path.
        schema_fields = build_template_data_edit_schema(
            fields,
            template_id=template_id,
            model_id=model_id,
            page_size=page_size,
            readonly=readonly,
            label=instruction or node.name or "Data Table",
        )
        schema = [f.to_dict() for f in schema_fields]

        # Seed columns for provisioning come from the template's column keys
        # (the alias-based headers the requester file uses) — set them so
        # DATA_EDIT's provision_artifacts skips the OData probe and uses the
        # requester file (file_id) directly as the table artifact.
        column_keys = [f.column_key for f in fields if f.column_key]

        ctx["file_id"] = file_id
        ctx["source_columns"] = column_keys
        ctx["instruction"] = instruction
        ctx["readonly"] = readonly
        ctx["query_params"] = {}
        ctx["actions"] = []  # action steps live inside the OdataWrapper schema
        ctx["extra_settings"] = []
        ctx["custom_data_update"] = False
        ctx["source_node_id"] = ""
        ctx["source_node_name"] = ""
        ctx["source_row_count"] = 0
        ctx["template_id"] = template_id
        ctx["business_data_model_id"] = model_id
        return schema

    def build_output_metadata(
        self,
        *,
        node: WorkflowNode,
        run: WorkflowRun,
        resolved_input: Any,
        prefilled: dict[str, Any],
        input_schema: list,
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        meta = super().build_output_metadata(
            node=node, run=run, resolved_input=resolved_input,
            prefilled=prefilled, input_schema=input_schema, ctx=ctx,
        )
        meta["node_type"] = "TEMPLATE_DATA_EDIT"
        meta["template_id"] = ctx.get("template_id", "")
        meta["business_data_model_id"] = ctx.get("business_data_model_id", "")
        return meta
