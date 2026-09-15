"""Environment — aggregate root for a named set of config variables (SA-2365).

An Environment is tenant-scoped and grouped by a free-text ``section`` (e.g.
"SimpleMDG"); its ``name`` is the user-defined environment name (dev/qa/prod/…).
A workflow selects an environment at run time; the engine resolves ``{{$env.KEY}}``
references against the selected environment's variables (see SA-2371).

Rich domain entity following the GatewayApp pattern: every mutation goes through
a behaviour method that validates the invariant and ``touch``-es the CAS/audit
metadata. Use cases never mutate fields directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid

from app.layer1_domain.entities.base_audit_entity import BaseAuditDomainEntity
from app.layer1_domain.exceptions import ValidationError
from app.layer1_domain.value_objects.env_variable import EnvVariable, key_error


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _name_error(name: str) -> str | None:
    if not name or not name.strip():
        return "environment name must not be empty"
    return None


@dataclass(kw_only=True)
class Environment(BaseAuditDomainEntity):
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str = "system"
    section: str = ""                              # grouping label
    name: str = ""                                 # env name (dev/qa/prod/…)
    variables: list[EnvVariable] = field(default_factory=list)
    is_active: bool = True
    # CAS counter; bumped on every save. PATCH callers pass version via If-Match.
    version: int = 1

    def __post_init__(self) -> None:
        # Tolerate variables passed as dicts (repo/DTO hydration).
        self.variables = [
            v if isinstance(v, EnvVariable) else EnvVariable(**v)
            for v in (self.variables or [])
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
        name: str,
        created_by: str,
        variables: list[EnvVariable] | None = None,
    ) -> "Environment":
        if not created_by or not created_by.strip():
            raise ValidationError("created_by must not be empty")
        env = cls(
            tenant_id=(tenant_id or "").strip() or "system",
            section=(section or "").strip(),
            name=(name or "").strip(),
            variables=list(variables or []),
            created_by=created_by,
            updated_by=created_by,
        )
        env._ensure_valid()
        return env

    # ── Behaviour (every mutation validates + touches) ───────────────
    def rename(self, name: str, *, updated_by: str) -> None:
        cleaned = (name or "").strip()
        err = _name_error(cleaned)
        if err:
            raise ValidationError(err)
        self.name = cleaned
        self.touch(updated_by)

    def set_section(self, section: str, *, updated_by: str) -> None:
        self.section = (section or "").strip()
        self.touch(updated_by)

    def set_variables(self, variables: list[EnvVariable], *, updated_by: str) -> None:
        """Replace the whole variable list (the editor sends the full set)."""
        self._validate_variables(variables)
        self.variables = list(variables)
        self.touch(updated_by)

    def upsert_variable(self, variable: EnvVariable, *, updated_by: str) -> None:
        merged = [v for v in self.variables if v.key != variable.key]
        merged.append(variable)
        self._validate_variables(merged)
        self.variables = merged
        self.touch(updated_by)

    def remove_variable(self, key: str, *, updated_by: str) -> None:
        self.variables = [v for v in self.variables if v.key != key]
        self.touch(updated_by)

    def activate(self, *, updated_by: str) -> None:
        self.is_active = True
        self.touch(updated_by)

    def deactivate(self, *, updated_by: str) -> None:
        self.is_active = False
        self.touch(updated_by)

    # ── Projections ──────────────────────────────────────────────────
    def get(self, key: str) -> EnvVariable | None:
        for v in self.variables:
            if v.key == key:
                return v
        return None

    def non_secret_env_map(self) -> dict:
        """Build the ``$env`` source injected into the run context at StartRun.

        No SECRET ever enters the context — that invariant (SA-2287) is intact.
        ``text`` maps to its string value, ``list`` to the list of values, and a
        ``credential`` variable maps to its **credential reference id**, which is
        NOT a secret (it is already public in the environment API and the FE
        picker). Carrying the ref lets a node reference the credential by name —
        e.g. ``credential_ref: {{$env.API_TOKEN}}`` — so switching environment
        switches which credential the node uses; the secret itself is still
        resolved from the vault at dispatch through the credential seam, never
        persisted here.
        """
        from app.layer1_domain.value_objects.env_variable import VariableType

        out: dict = {}
        for v in self.variables:
            out[v.key] = list(v.values) if v.type is VariableType.LIST else v.value
        return out

    def suggestion_env_map(self) -> dict:
        """The ``$env`` source for the DESIGN-TIME node variable-suggestion panel.

        Same keys as :meth:`non_secret_env_map`. A ``credential`` variable
        contributes its **credential-ref id** (non-secret — already public in the
        environment API and the FE picker) so a designer can SEE which credential
        the env maps to and tell environments apart (e.g. dev vs qas). The secret
        itself is never here; it is fetched from the vault at dispatch. ``text``
        keeps its value and ``list`` its pool so the panel can hint what
        ``{{$env.KEY}}`` will yield.
        """
        from app.layer1_domain.value_objects.env_variable import VariableType

        out: dict = {}
        for v in self.variables:
            if v.type is VariableType.CREDENTIAL:
                out[v.key] = v.value  # credential-ref id (non-secret), so dev/qas are distinguishable
            elif v.type is VariableType.LIST:
                out[v.key] = list(v.values)
            else:
                out[v.key] = v.value
        return out

    def to_public_dict(self) -> dict:
        """Serializable projection for API responses. Config variable values are
        non-secret (a credential-type variable only carries a credential_ref id,
        never a secret), so they are safe to return."""
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "section": self.section,
            "name": self.name,
            "is_active": self.is_active,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "variables": [
                {
                    "key": v.key,
                    "type": v.type.value,
                    "value": v.value,
                    "values": list(v.values),
                    "is_secret": v.is_secret,
                }
                for v in self.variables
            ],
        }

    # ── Invariants ───────────────────────────────────────────────────
    @staticmethod
    def _validate_variables(variables: list[EnvVariable]) -> None:
        errors: list[str] = []
        seen: set[str] = set()
        for v in variables:
            errors.extend(v.validate())
            if v.key in seen:
                errors.append(f"duplicate variable key '{v.key}'")
            seen.add(v.key)
        if errors:
            raise ValidationError("; ".join(errors))

    def validate(self) -> list[str]:
        errors: list[str] = []
        name_problem = _name_error(self.name)
        if name_problem:
            errors.append(name_problem)
        if not self.tenant_id or not self.tenant_id.strip():
            errors.append("tenant_id is required")
        seen: set[str] = set()
        for v in self.variables:
            errors.extend(v.validate())
            if v.key in seen:
                errors.append(f"duplicate variable key '{v.key}'")
            seen.add(v.key)
        return errors

    def _ensure_valid(self) -> None:
        errors = self.validate()
        if errors:
            raise ValidationError("; ".join(errors))

    def touch(self, updated_by: str) -> None:
        """Bump version + updated_at/updated_by. Called by every mutator."""
        self.version += 1
        self.updated_at = _utc_now_iso()
        self.updated_by = updated_by
