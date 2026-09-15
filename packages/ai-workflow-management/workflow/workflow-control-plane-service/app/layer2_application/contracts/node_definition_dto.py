"""Strict pydantic DTOs for the unified node catalog.

These are the response shapes for the (Phase 1) ``/v2/node-palette`` and
``/v2/node-definitions/{id}`` endpoints. Phase 0 ships them as importable
shapes only; no endpoint returns them yet.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.layer1_domain.value_objects.node_kind import MINE_COLLECTION, NodeKind
from app.layer2_application.contracts.form_schema import FormField

# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------


NodeProviderKind = Literal["builtin", "worker", "business"]


class NodeVisibility(str, Enum):
    VISIBLE = "visible"
    HIDDEN = "hidden"
    DEPRECATED = "deprecated"


class NodeRuntime(str, Enum):
    """Where the node executes."""
    ENGINE = "engine"      # CONDITION, SWITCH, LOOP, PARALLEL, MERGE, COMPUTE
    WORKER = "worker"      # TASK dispatched to a registered worker
    PENDING = "pending"    # INPUT, HUMAN_ACTION, DATA_EDIT, FILE_UPLOAD


# ---------------------------------------------------------------------------
# Leaf models
# ---------------------------------------------------------------------------


class NodePort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    label: str = ""


class NodePorts(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    inputs: list[NodePort] = Field(default_factory=list, alias="in")
    outputs: list[NodePort] = Field(default_factory=list, alias="out")


class NodeCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = ""
    action: str = ""


class NodeCategoryInfo(BaseModel):
    """Display metadata for a single category key in the palette tree."""
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    order: int = 0


# ---------------------------------------------------------------------------
# Summary (list view) and Detail (full with schemas)
# ---------------------------------------------------------------------------


class NodeDefinitionSummary(BaseModel):
    """Compact entry for /v2/node-palette list payloads."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1)
    name: str = ""
    description: str = ""
    provider: NodeProviderKind
    node_type: str = Field(alias="nodeType")
    category: str = "general"
    kind: NodeKind = NodeKind.ACTION
    collection: str = MINE_COLLECTION
    icon: str = "Cog"
    color: str = "#3B82F6"
    tags: list[str] = Field(default_factory=list)
    spec_version: str = Field(default="1.0.0", alias="specVersion")
    visibility: NodeVisibility = NodeVisibility.VISIBLE
    runtime: NodeRuntime
    ports: NodePorts = Field(default_factory=NodePorts)


class NodeDefinitionDetail(NodeDefinitionSummary):
    """Full entry with input/output schema and provider-specific metadata.

    Served by /v2/node-definitions/{id} in Phase 1.
    """
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    input_schema: list[FormField] = Field(default_factory=list, alias="inputSchema")
    output_schema: list[FormField] = Field(default_factory=list, alias="outputSchema")
    capabilities: list[NodeCapability] = Field(default_factory=list)
    # Provider-specific extras, intentionally generic. Phase 1 may split these
    # into typed subclasses per provider.
    worker_type: Optional[str] = Field(default=None, alias="workerType")
    base_worker_id: Optional[str] = Field(default=None, alias="baseWorkerId")
    base_node_type: Optional[str] = Field(default=None, alias="baseNodeType")
    parent_definition_id: Optional[str] = Field(default=None, alias="parentDefinitionId")
    locked_defaults: dict[str, Any] = Field(
        default_factory=dict, alias="lockedDefaults"
    )
    input_mapping: dict[str, Any] = Field(
        default_factory=dict, alias="inputMapping"
    )
    output_mapping: dict[str, Any] = Field(
        default_factory=dict, alias="outputMapping"
    )
    data_mode: str = Field(default="json", alias="dataMode")
    edit_mode: str = Field(default="none", alias="editMode")
    # Credential picker metadata (T1/F9/F10): the vault credential kinds this node
    # accepts and its provider affinity. Empty list = node takes no credential.
    supported_credential_kinds: list[str] = Field(
        default_factory=list, alias="supportedCredentialKinds"
    )
    credential_provider: Optional[str] = Field(
        default=None, alias="credentialProvider"
    )
