"""EnvSectionSchema — the shared variable KEY SCHEMA for a Section (SA-2471).

SA-2365 gave each Environment its own free-form variable list. SA-2471 promotes
the *key schema* (the set of ``{key, type}`` pairs) to the SECTION level: every
Environment in a Section shares the SAME keys and types, and only the VALUES
differ per environment (a matrix). This entity is that per-``(tenant, section)``
schema.

Environments keep storing their own per-env values (SA-2365 ``AIW_ENVIRONMENT``),
but their key set is *governed* by this schema — creating/updating a schema
RECONCILES every environment in the section to the new key set (see the
reconcile application service), and an environment save is validated against the
schema (its keys must be a subset).

Rich domain entity following the ``Environment`` / GatewayApp pattern: every
mutation goes through a behaviour method that validates the invariant and
``touch``-es the CAS/audit metadata. Use cases never mutate fields directly.

``validate()`` returns *structured* errors (not a flat string) so the API can
surface exactly which key is duplicate / empty / invalid:

    {
        "duplicate_keys":     ["API_URL"],   # case-insensitive collisions
        "empty_key_indexes":  [2],           # positions with a blank key
        "invalid_keys":       ["bad key!"],  # fail the EnvVariable key format
    }

The keys list MAY be empty — an empty section (no shared variables yet) is a
valid state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid

from app.layer1_domain.entities.base_audit_entity import BaseAuditDomainEntity
from app.layer1_domain.exceptions import ValidationError
from app.layer1_domain.value_objects.env_variable import VariableType, key_error


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, kw_only=True)
class SchemaKey:
    """One entry in a section schema: a variable key plus its type. The value
    lives per-environment; the schema owns only the key and its type."""

    key: str
    type: VariableType = VariableType.TEXT

    def __post_init__(self) -> None:
        if isinstance(self.type, str):
            object.__setattr__(self, "type", VariableType(self.type))

    @classmethod
    def from_dict(cls, d: dict) -> "SchemaKey":
        return cls(
            key=(d.get("key") or "").strip(),
            type=VariableType(d.get("type") or "text"),
        )

    @staticmethod
    def list_from_dicts(items: list[dict] | None) -> list["SchemaKey"]:
        return [SchemaKey.from_dict(d) for d in (items or [])]

    def to_dict(self) -> dict:
        return {"key": self.key, "type": self.type.value}


@dataclass
class SchemaKeyErrors:
    """Structured validation result for a schema's keys (SA-2471)."""

    duplicate_keys: list[str] = field(default_factory=list)
    empty_key_indexes: list[int] = field(default_factory=list)
    invalid_keys: list[str] = field(default_factory=list)

    def is_ok(self) -> bool:
        return not (self.duplicate_keys or self.empty_key_indexes or self.invalid_keys)

    def to_dict(self) -> dict:
        return {
            "duplicate_keys": list(self.duplicate_keys),
            "empty_key_indexes": list(self.empty_key_indexes),
            "invalid_keys": list(self.invalid_keys),
        }


@dataclass(kw_only=True)
class EnvSectionSchema(BaseAuditDomainEntity):
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str = "system"
    section: str = ""                              # grouping label (natural key)
    keys: list[SchemaKey] = field(default_factory=list)
    is_active: bool = True
    # CAS counter; bumped on every save. PATCH callers pass version via If-Match.
    version: int = 1

    def __post_init__(self) -> None:
        # Tolerate keys passed as dicts (repo/DTO hydration).
        self.keys = [
            k if isinstance(k, SchemaKey) else SchemaKey.from_dict(k)
            for k in (self.keys or [])
        ]
        if not self.created_at:
            self.created_at = _utc_now_iso()
        if not self.updated_at:
            self.updated_at = self.created_at

    # ── Factory ──────────────────────────────────────────────────────
    @classmethod
    def create(
        cls,
        *,
        tenant_id: str,
        section: str,
        keys: list[SchemaKey] | None = None,
        created_by: str,
    ) -> "EnvSectionSchema":
        if not created_by or not created_by.strip():
            raise ValidationError("created_by must not be empty")
        schema = cls(
            tenant_id=(tenant_id or "").strip() or "system",
            section=(section or "").strip(),
            keys=list(keys or []),
            created_by=created_by,
            updated_by=created_by,
        )
        schema._ensure_valid()
        return schema

    # ── Behaviour (every mutation validates + touches) ───────────────
    def set_keys(self, new_keys: list[SchemaKey], *, updated_by: str) -> None:
        """Replace the whole key schema (the matrix editor sends the full set)."""
        candidate = list(new_keys)
        errors = self._validate_keys(candidate)
        if not errors.is_ok():
            raise ValidationError(f"invalid section schema keys: {errors.to_dict()}")
        self.keys = candidate
        self.touch(updated_by)

    def rename(self, new_section: str, *, updated_by: str) -> None:
        """Rename the section (SA-2450 W18). The caller reconciles every
        environment in the OLD section to the new name (env ids are stable; the
        section string is the grouping key). Uniqueness of (tenant, section) is
        enforced by the repo on save."""
        cleaned = (new_section or "").strip()
        if not cleaned:
            raise ValidationError("section name must not be empty")
        self.section = cleaned
        self.touch(updated_by)

    def touch(self, updated_by: str) -> None:
        """Bump version + updated_at/updated_by. Called by every mutator."""
        self.version += 1
        self.updated_at = _utc_now_iso()
        self.updated_by = updated_by

    def deactivate(self, *, updated_by: str) -> None:
        self.is_active = False
        self.touch(updated_by)

    # ── Projections ──────────────────────────────────────────────────
    def key_names(self) -> list[str]:
        return [k.key for k in self.keys]

    def type_for(self, key: str) -> VariableType | None:
        for k in self.keys:
            if k.key == key:
                return k.type
        return None

    def to_public_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "section": self.section,
            "keys": [k.to_dict() for k in self.keys],
            "is_active": self.is_active,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    # ── Invariants ───────────────────────────────────────────────────
    @staticmethod
    def _validate_keys(keys: list[SchemaKey]) -> SchemaKeyErrors:
        result = SchemaKeyErrors()
        seen: dict[str, str] = {}                    # lower-key -> first-seen key
        reported_dups: set[str] = set()
        for idx, k in enumerate(keys):
            raw = (k.key or "").strip()
            if not raw:
                result.empty_key_indexes.append(idx)
                continue
            if key_error(raw) is not None:
                result.invalid_keys.append(raw)
                # An invalid key is still checked for duplicates so the caller
                # sees every problem, but a bad key can't collide meaningfully;
                # fall through to the dup check below on its lowercased form.
            lower = raw.lower()
            if lower in seen and lower not in reported_dups:
                result.duplicate_keys.append(raw)
                reported_dups.add(lower)
            else:
                seen.setdefault(lower, raw)
        return result

    def validate(self) -> SchemaKeyErrors:
        """Structured validation of the current key set."""
        return self._validate_keys(self.keys)

    def _ensure_valid(self) -> None:
        errors = self.validate()
        if not errors.is_ok():
            raise ValidationError(f"invalid section schema keys: {errors.to_dict()}")
