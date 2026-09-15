from __future__ import annotations

import re
from dataclasses import dataclass

from app.layer1_domain.exceptions import ValidationError

# ``{name}`` placeholder tokens. Names are alphanumeric + underscore.
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
# Detect unbalanced/invalid braces left over after stripping valid placeholders.
_VALID_AFTER_STRIP_RE = re.compile(r"[{}]")


@dataclass(frozen=True)
class PathTemplate:
    """A request path with ``{placeholder}`` tokens, e.g. ``/orders/{order_id}``.

    Self-validating: non-empty, balanced/well-formed placeholders. The
    relative-vs-absolute rule (internal apps want a leading ``/``) is enforced by
    the owning entity, which knows the app kind.
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value or not self.value.strip():
            raise ValidationError("path_template must not be empty")
        if self.value != self.value.strip():
            raise ValidationError("path_template must not have surrounding whitespace")
        # Every brace must belong to a well-formed {name} placeholder.
        stripped = _PLACEHOLDER_RE.sub("", self.value)
        if _VALID_AFTER_STRIP_RE.search(stripped):
            raise ValidationError(
                f"path_template '{self.value}' has malformed placeholders "
                "(use {name} with alphanumeric/underscore names)"
            )

    def placeholders(self) -> frozenset[str]:
        """The set of ``{name}`` tokens referenced by this template."""
        return frozenset(_PLACEHOLDER_RE.findall(self.value))

    def is_relative(self) -> bool:
        return self.value.startswith("/")
