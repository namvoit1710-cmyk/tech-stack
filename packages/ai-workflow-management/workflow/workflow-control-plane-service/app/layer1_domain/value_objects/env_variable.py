"""EnvVariable — a single named value inside an Environment (SA-2365).

An Environment groups values that vary per environment (dev/qa/prod) so a node
field can reference them via ``{{$env.KEY}}`` instead of scattering them across
each worker's ``.env``. Each variable carries a ``type`` that drives how it is
resolved at run time:

- ``text``       — a plain string value (base ids, base URLs, flags, account
                   names). A base URL is just text — there is no separate type
                   for it (SA-2378: nested per-URL services were not adopted).
- ``credential`` — ``value`` holds a **credential_ref** (an id in the credentials
                   vault). The secret itself never lives here; resolution pulls it
                   from the vault server-side (SA-2371).
- ``list``       — a pool of string values in ``values`` (e.g. the claude token
                   list). A run picks one via round-robin selection (SA-2372).

This is a frozen value object: an Environment mutates by *replacing* its variable
list, never by mutating a variable in place — mirrors how the codebase treats
other value objects and keeps the aggregate's ``touch``/CAS the single write path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.layer1_domain.exceptions import ValidationError


class VariableType(StrEnum):
    TEXT = "text"
    CREDENTIAL = "credential"
    LIST = "list"


# Keys are referenced as ``{{$env.KEY}}``; the resolver's EXPRESSION_PATTERN is a
# dotted path, so a key must be a bare identifier segment (no dots/spaces/braces).
_VALID_KEY = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
)


def key_error(key: str) -> str | None:
    """Return an error message if ``key`` is not a usable ``$env`` reference key."""
    if not key or not key.strip():
        return "variable key must not be empty"
    if any(ch not in _VALID_KEY for ch in key):
        return (
            f"variable key '{key}' has invalid characters; use letters, digits, "
            "'_' or '-' only (referenced as {{$env.KEY}})"
        )
    return None


@dataclass(frozen=True, kw_only=True)
class EnvVariable:
    key: str
    type: VariableType = VariableType.TEXT
    value: str = ""                       # text / credential(ref)
    values: tuple[str, ...] = ()          # list type only (token pool)

    def __post_init__(self) -> None:
        if isinstance(self.type, str):
            object.__setattr__(self, "type", VariableType(self.type))
        if isinstance(self.values, list):
            object.__setattr__(self, "values", tuple(self.values))

    def validate(self) -> list[str]:
        errors: list[str] = []
        err = key_error(self.key)
        if err:
            errors.append(err)
        if self.type is VariableType.LIST:
            if not self.values:
                errors.append(f"list variable '{self.key}' requires at least one value")
        else:
            if self.values:
                errors.append(
                    f"variable '{self.key}' of type '{self.type}' must use 'value', not 'values'"
                )
            # A credential variable references a vault credential id. An EMPTY value
            # means "not configured for this environment yet" — valid in the
            # section-shared-schema model (SA-2471): a new environment inherits every
            # section key, including credential keys, unset, and is filled in per
            # environment later. Runtime $env resolution already errors clearly if a
            # credential is still unset at run time, so an empty value is allowed here.
        return errors

    @property
    def is_secret(self) -> bool:
        """A credential-typed variable resolves through the vault; its resolved
        value must be redacted in logs/evidence (SA-2371)."""
        return self.type is VariableType.CREDENTIAL

    @classmethod
    def make(
        cls,
        *,
        key: str,
        type: VariableType | str = VariableType.TEXT,
        value: str = "",
        values: tuple[str, ...] | list[str] = (),
    ) -> "EnvVariable":
        var = cls(
            key=(key or "").strip(),
            type=VariableType(type) if isinstance(type, str) else type,
            value=value or "",
            values=tuple(values),
        )
        errors = var.validate()
        if errors:
            raise ValidationError("; ".join(errors))
        return var

    @classmethod
    def from_dict(cls, d: dict) -> "EnvVariable":
        """Build (and validate) a variable from an API/DTO dict."""
        return cls.make(
            key=d.get("key", ""),
            type=d.get("type", "text") or "text",
            value=d.get("value", "") or "",
            values=tuple(d.get("values") or ()),
        )

    @staticmethod
    def list_from_dicts(items: list[dict] | None) -> list["EnvVariable"]:
        return [EnvVariable.from_dict(d) for d in (items or [])]
