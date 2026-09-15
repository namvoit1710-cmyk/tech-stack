"""GatewayApp — aggregate root for a registered upstream application.

Rich domain entity: every field change goes through a behaviour method that
enforces the relevant invariant and bumps the CAS/audit metadata via ``touch``.
Use cases never mutate fields directly. An ``GatewayApp`` instance that exists
is always valid (``create`` and each mutator guarantee it).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid

from app.layer1_domain.entities.base_audit_entity import BaseAuditDomainEntity
from app.layer1_domain.exceptions import ValidationError
from app.layer1_domain.value_objects.auth_type import AuthType
from app.layer1_domain.value_objects.encrypted_credential import EncryptedCredential
from app.layer1_domain.value_objects.gateway.app_kind import AppKind
from app.layer1_domain.value_objects.gateway.host_allowlist import HostAllowlist
from app.layer1_domain.value_objects.gateway.resource_name import name_error


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(kw_only=True)
class GatewayApp(BaseAuditDomainEntity):
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    kind: AppKind = AppKind.INTERNAL
    base_url: str = ""                                  # internal apps
    allowed_hosts: HostAllowlist | None = None          # external apps
    auth_type: AuthType = AuthType.NONE
    auth_config_encrypted: EncryptedCredential | None = None
    is_active: bool = True
    description: str = ""
    # OpenAPI spec sync — keep this app's functions in sync with an upstream spec.
    spec_url: str = ""            # OpenAPI document URL; empty = no sync
    last_synced_at: str = ""      # ISO timestamp of last successful sync; "" = never
    spec_hash: str = ""           # sha256 of the last fetched spec body (change detection)
    # CAS counter; bumped on every save. PATCH callers pass version via If-Match.
    version: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.kind, str):
            self.kind = AppKind(self.kind)
        if isinstance(self.auth_type, str):
            self.auth_type = AuthType(self.auth_type)
        if isinstance(self.allowed_hosts, (list, tuple)):
            self.allowed_hosts = HostAllowlist.from_list(list(self.allowed_hosts))
        if not self.created_at:
            self.created_at = _utc_now_iso()
        if not self.updated_at:
            self.updated_at = self.created_at

    # ── Factory ──────────────────────────────────────────────────────
    @classmethod
    def create(
        cls,
        *,
        name: str,
        kind: AppKind,
        created_by: str,
        base_url: str = "",
        allowed_hosts: HostAllowlist | None = None,
        auth_type: AuthType = AuthType.NONE,
        auth_config_encrypted: EncryptedCredential | None = None,
        description: str = "",
        spec_url: str = "",
    ) -> "GatewayApp":
        if not created_by or not created_by.strip():
            raise ValidationError("created_by must not be empty")
        app = cls(
            name=(name or "").strip(),
            kind=kind,
            base_url=(base_url or "").strip(),
            allowed_hosts=allowed_hosts,
            auth_type=auth_type,
            auth_config_encrypted=auth_config_encrypted,
            description=(description or "").strip(),
            spec_url=(spec_url or "").strip(),
            created_by=created_by,
            updated_by=created_by,
        )
        app._ensure_valid()
        return app

    # ── Behaviour (every mutation validates + touches) ───────────────
    def rename(self, name: str, *, updated_by: str) -> None:
        cleaned = (name or "").strip()
        # Same rule as create: renaming into an unaddressable name would strand
        # the row just as surely as creating one.
        error = name_error(cleaned)
        if error:
            raise ValidationError(error)
        self.name = cleaned
        self.touch(updated_by)

    def change_description(self, description: str, *, updated_by: str) -> None:
        self.description = (description or "").strip()
        self.touch(updated_by)

    def change_base_url(self, base_url: str, *, updated_by: str) -> None:
        if self.kind is not AppKind.INTERNAL:
            raise ValidationError("base_url applies only to internal apps")
        cleaned = (base_url or "").strip()
        if not cleaned:
            raise ValidationError("base_url must not be empty")
        self.base_url = cleaned
        self.touch(updated_by)

    def set_allowed_hosts(self, hosts: HostAllowlist, *, updated_by: str) -> None:
        if self.kind is not AppKind.EXTERNAL:
            raise ValidationError("allowed_hosts applies only to external apps")
        if hosts.is_empty():
            raise ValidationError("external apps require at least one allowed host")
        self.allowed_hosts = hosts
        self.touch(updated_by)

    def set_auth(
        self,
        auth_type: AuthType,
        credential: EncryptedCredential | None,
        *,
        updated_by: str,
    ) -> None:
        """Set/rotate the app credential. ``credential`` is the already-encrypted
        blob (the cipher lives in infrastructure); the domain only enforces that
        a non-NONE auth type carries one."""
        if auth_type is not AuthType.NONE and credential is None:
            raise ValidationError(
                f"auth_type '{auth_type.value}' requires a credential"
            )
        self.auth_type = auth_type
        self.auth_config_encrypted = None if auth_type is AuthType.NONE else credential
        self.touch(updated_by)

    def clear_auth(self, *, updated_by: str) -> None:
        self.set_auth(AuthType.NONE, None, updated_by=updated_by)

    def set_spec_url(self, spec_url: str, *, updated_by: str) -> None:
        """Point the app at an OpenAPI spec URL (empty string clears it)."""
        self.spec_url = (spec_url or "").strip()
        self.touch(updated_by)

    def mark_synced(self, spec_hash: str, *, updated_by: str) -> None:
        """Record a successful spec sync: store the body hash + sync time. Bumps
        version/audit like any mutation so the save goes through the normal CAS."""
        self.spec_hash = spec_hash or ""
        self.last_synced_at = _utc_now_iso()
        self.touch(updated_by)

    def activate(self, *, updated_by: str) -> None:
        self.is_active = True
        self.touch(updated_by)

    def deactivate(self, *, updated_by: str) -> None:
        self.is_active = False
        self.touch(updated_by)

    # ── Invariants / projections ─────────────────────────────────────
    def validate(self) -> list[str]:
        """Return invariant-violation messages (empty = valid)."""
        errors: list[str] = []
        # The name is the route key and the unique index key, not a label.
        name_problem = name_error(self.name)
        if name_problem:
            errors.append(name_problem)
        if self.kind is AppKind.INTERNAL:
            if not self.base_url or not self.base_url.strip():
                errors.append("internal apps require base_url")
        elif self.kind is AppKind.EXTERNAL:
            if self.allowed_hosts is None or self.allowed_hosts.is_empty():
                errors.append("external apps require at least one allowed host")
        if self.auth_type is not AuthType.NONE and self.auth_config_encrypted is None:
            errors.append(f"auth_type '{self.auth_type.value}' requires a credential")
        return errors

    def _ensure_valid(self) -> None:
        errors = self.validate()
        if errors:
            raise ValidationError("; ".join(errors))

    def credential_summary(self) -> dict:
        """Redacted credential metadata for projections (never the secret)."""
        cred = self.auth_config_encrypted
        return {
            "credential_present": bool(cred and cred.ciphertext_b64),
            "key_version": cred.key_version if cred else 0,
            "last_rotated_at": self.updated_at or "",
        }

    def touch(self, updated_by: str) -> None:
        """Bump version + updated_at/updated_by. Called by every mutator."""
        self.version += 1
        self.updated_at = _utc_now_iso()
        self.updated_by = updated_by
