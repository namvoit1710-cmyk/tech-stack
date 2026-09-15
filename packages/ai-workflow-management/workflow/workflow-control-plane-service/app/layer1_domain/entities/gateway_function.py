"""GatewayFunction — a named operation exposed by a GatewayApp.

Rich domain entity. The request signature (method + path_template + input params)
carries a coupled invariant — every ``{placeholder}`` in the path must have a
matching ``path``-located param and vice-versa — so it is mutated atomically via
``set_signature``. All other fields have their own behaviour methods. ``app_id``
is immutable after creation.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid

from app.layer1_domain.entities.base_audit_entity import BaseAuditDomainEntity
from app.layer1_domain.exceptions import ValidationError
from app.layer1_domain.value_objects.gateway.function_param import FunctionParam
from app.layer1_domain.value_objects.gateway.http_method import HttpMethod
from app.layer1_domain.value_objects.gateway.output_field import OutputField
from app.layer1_domain.value_objects.gateway.param_location import ParamLocation
from app.layer1_domain.value_objects.gateway.path_template import PathTemplate
from app.layer1_domain.value_objects.gateway.resource_name import name_error

DEFAULT_TIMEOUT_MS = 30_000


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(kw_only=True)
class GatewayFunction(BaseAuditDomainEntity):
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    app_id: str = ""
    name: str = ""
    description: str = ""
    method: HttpMethod = HttpMethod.GET
    path_template: PathTemplate | None = None
    input_params: tuple[FunctionParam, ...] = ()
    output_fields: tuple[OutputField, ...] = ()
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    icon: str = ""
    color: str = ""
    tags: tuple[str, ...] = ()
    is_active: bool = True
    version: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.method, str):
            self.method = HttpMethod.coerce(self.method)
        if isinstance(self.path_template, str):
            self.path_template = PathTemplate(self.path_template)
        self.input_params = tuple(self.input_params)
        self.output_fields = tuple(self.output_fields)
        self.tags = tuple(self.tags)
        if not self.created_at:
            self.created_at = _utc_now_iso()
        if not self.updated_at:
            self.updated_at = self.created_at

    # ── Factory ──────────────────────────────────────────────────────
    @classmethod
    def create(
        cls,
        *,
        app_id: str,
        name: str,
        method: HttpMethod,
        path_template: PathTemplate,
        created_by: str,
        input_params: Sequence[FunctionParam] = (),
        output_fields: Sequence[OutputField] = (),
        description: str = "",
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        icon: str = "",
        color: str = "",
        tags: Sequence[str] = (),
    ) -> "GatewayFunction":
        if not app_id or not app_id.strip():
            raise ValidationError("app_id must not be empty")
        if not created_by or not created_by.strip():
            raise ValidationError("created_by must not be empty")
        fn = cls(
            app_id=app_id.strip(),
            name=(name or "").strip(),
            method=method,
            path_template=path_template,
            input_params=tuple(input_params),
            output_fields=tuple(output_fields),
            description=(description or "").strip(),
            timeout_ms=int(timeout_ms),
            icon=icon,
            color=color,
            tags=tuple(tags),
            created_by=created_by,
            updated_by=created_by,
        )
        fn._ensure_valid()
        return fn

    # ── Behaviour ────────────────────────────────────────────────────
    def rename(self, name: str, *, updated_by: str) -> None:
        cleaned = (name or "").strip()
        # Functions are addressed the same way apps are —
        # /api/v1/gateway/apps/{app_name}/functions/{name} — so the same rule.
        error = name_error(cleaned)
        if error:
            raise ValidationError(error)
        self.name = cleaned
        self.touch(updated_by)

    def change_description(self, description: str, *, updated_by: str) -> None:
        self.description = (description or "").strip()
        self.touch(updated_by)

    def set_signature(
        self,
        *,
        updated_by: str,
        method: HttpMethod | None = None,
        path_template: PathTemplate | None = None,
        input_params: Sequence[FunctionParam] | None = None,
    ) -> None:
        """Atomically change any of method / path_template / input_params.

        Applied together because the path↔param invariant couples them; the
        resulting signature is validated before the change is accepted.
        """
        new_method = method if method is not None else self.method
        new_path = path_template if path_template is not None else self.path_template
        new_params = (
            tuple(input_params) if input_params is not None else self.input_params
        )
        self._check_signature(new_path, new_params)
        self.method = new_method
        self.path_template = new_path
        self.input_params = new_params
        self.touch(updated_by)

    def set_output_fields(
        self, output_fields: Sequence[OutputField], *, updated_by: str
    ) -> None:
        self.output_fields = tuple(output_fields)
        self.touch(updated_by)

    def change_timeout(self, timeout_ms: int, *, updated_by: str) -> None:
        if timeout_ms <= 0:
            raise ValidationError("timeout_ms must be positive")
        self.timeout_ms = int(timeout_ms)
        self.touch(updated_by)

    def set_presentation(
        self,
        *,
        updated_by: str,
        icon: str | None = None,
        color: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> None:
        if icon is not None:
            self.icon = icon
        if color is not None:
            self.color = color
        if tags is not None:
            self.tags = tuple(tags)
        self.touch(updated_by)

    def activate(self, *, updated_by: str) -> None:
        self.is_active = True
        self.touch(updated_by)

    def deactivate(self, *, updated_by: str) -> None:
        self.is_active = False
        self.touch(updated_by)

    # ── Invariants ───────────────────────────────────────────────────
    @staticmethod
    def _check_signature(
        path_template: PathTemplate | None,
        input_params: tuple[FunctionParam, ...],
    ) -> None:
        if path_template is None:
            raise ValidationError("path_template is required")
        placeholders = path_template.placeholders()
        path_param_keys = {
            p.key for p in input_params if p.location is ParamLocation.PATH
        }
        missing_params = placeholders - path_param_keys
        if missing_params:
            raise ValidationError(
                f"path_template placeholders without a matching path param: "
                f"{sorted(missing_params)}"
            )
        orphan_path_params = path_param_keys - placeholders
        if orphan_path_params:
            raise ValidationError(
                f"path params not referenced by path_template: {sorted(orphan_path_params)}"
            )
        keys = [p.key for p in input_params]
        if len(keys) != len(set(keys)):
            raise ValidationError("duplicate input param keys")

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.app_id or not self.app_id.strip():
            errors.append("app_id is required")
        name_problem = name_error(self.name)
        if name_problem:
            errors.append(name_problem)
        if self.timeout_ms <= 0:
            errors.append("timeout_ms must be positive")
        try:
            self._check_signature(self.path_template, self.input_params)
        except ValidationError as exc:
            errors.append(str(exc))
        return errors

    def _ensure_valid(self) -> None:
        errors = self.validate()
        if errors:
            raise ValidationError("; ".join(errors))

    def touch(self, updated_by: str) -> None:
        self.version += 1
        self.updated_at = _utc_now_iso()
        self.updated_by = updated_by
