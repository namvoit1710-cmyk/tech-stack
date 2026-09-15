"""NodeDefinition entity — business node template wrapping a base worker/control-flow node."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid

from app.layer1_domain.entities.base_entity import BaseDomainEntity
from app.layer1_domain.value_objects.node_kind import (
    MINE_COLLECTION,
    VALID_NODE_KINDS,
    default_kind,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.worker_ref import WorkerRef


@dataclass(kw_only=True)
class NodeDefinition(BaseDomainEntity):
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str = "system"
    name: str = ""
    description: str = ""
    # Contract identity of this business-node template (semver).
    # Phase 0: default only, no resolution rule yet.
    spec_version: str = "1.0.0"
    parent_definition_id: str | None = None
    base_type: NodeType = NodeType.TASK
    base_worker_id: str | None = None
    locked_defaults: dict[str, Any] = field(default_factory=dict)
    input_schema: list[dict[str, Any]] = field(default_factory=list)
    output_schema: list[dict[str, Any]] = field(default_factory=list)
    input_mapping: dict[str, Any] = field(default_factory=dict)
    output_mapping: dict[str, Any] = field(default_factory=dict)
    icon: str = ""
    color: str = ""
    tags: list[str] = field(default_factory=list)
    ports: dict[str, Any] = field(default_factory=dict)
    # Credential picker metadata (F9/F10): which vault credential kinds this node
    # accepts, and its provider affinity. The credential_ref itself is per-instance
    # node config (reserved input key), consumed by the worker coalesce — not here.
    supported_credential_kinds: list[str] = field(default_factory=list)
    credential_provider: str | None = None
    data_mode: str = "json"
    edit_mode: str = "none"
    kind: str = ""
    collection: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        if isinstance(self.base_type, str):
            self.base_type = NodeType(self.base_type)
        # Invariant: base_worker_id is the bare worker_type the executor keys on.
        # The catalog exposes workers as the id ``worker:<type>``; strip that
        # prefix here so a catalog id stored as base_worker_id (e.g. by the FE)
        # is normalized on every construction — including repo reads, which heals
        # existing rows without a migration.
        ref = WorkerRef.parse(self.base_worker_id)
        if ref is not None:
            self.base_worker_id = ref.worker_type
        # SA-1736: heal (kind, collection) on read — no migration (mirrors base_worker_id heal).
        if not self.kind:
            self.kind = default_kind(self.base_type, self.locked_defaults)
        if not self.collection:
            self.collection = MINE_COLLECTION

    VALID_DATA_MODES = ("json", "csv_single", "csv_multi")
    VALID_EDIT_MODES = ("none", "before_dispatch", "after_dispatch")
    # Vault CredentialKind vocabulary (design-time picker filter). MUST match the
    # canonical kinds credentials-management emits from GET /api/v1/credential-types —
    # the FE picker filters with an exact, case-sensitive kinds.includes(credentialKind)
    # (credential-filter.ts), so any case drift silently empties the picker. The Teams
    # webhook kind is WEBHOOK_URL (uppercase, per credentials-management ms-teams-webhook);
    # SA-1558 fixed it from the earlier lowercase "webhook_url" which never matched.
    # Distinct from the worker {type} vocab.
    VALID_CREDENTIAL_KINDS = (
        "API_KEY", "PERSONAL_ACCESS_TOKEN", "BASIC_AUTH", "JWT",
        "OAUTH2_DELEGATED", "OAUTH2_APPLICATION", "WEBHOOK_URL", "DATABASE_CONNECTION",
    )

    def validate(self) -> list[str]:
        """Return a list of validation error messages (empty = valid)."""
        errors: list[str] = []
        if not self.name or not self.name.strip():
            errors.append("name is required")
        if not self.tenant_id or not self.tenant_id.strip():
            errors.append("tenant_id is required")
        if self.parent_definition_id and self.parent_definition_id == self.id:
            errors.append("parent_definition_id cannot reference itself")
        # base_worker_id is only required for TASK nodes that have no parent
        # (children inherit base_worker_id from the parent chain)
        if self.base_type == NodeType.TASK and not self.base_worker_id and not self.parent_definition_id:
            errors.append("base_worker_id is required when base_type is TASK")
        if self.data_mode not in self.VALID_DATA_MODES:
            errors.append(f"data_mode must be one of {self.VALID_DATA_MODES}")
        if self.edit_mode not in self.VALID_EDIT_MODES:
            errors.append(f"edit_mode must be one of {self.VALID_EDIT_MODES}")
        for kind in self.supported_credential_kinds:
            if kind not in self.VALID_CREDENTIAL_KINDS:
                errors.append(
                    f"unknown credential kind {kind!r}; must be one of {self.VALID_CREDENTIAL_KINDS}"
                )
        if self.kind and self.kind not in VALID_NODE_KINDS:
            errors.append(f"kind must be one of {VALID_NODE_KINDS}")
        return errors
