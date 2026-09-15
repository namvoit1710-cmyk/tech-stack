"""Template-expression syntax checking — the single tokenizer truth.

The ``{{$source.path[0].key}}`` pattern lived as a private regex in
``layer2_application/resolve/variable_resolver.py``; it is defined
here (layer 1) so save-time validation and the runtime resolver share
one grammar. The resolver imports ``EXPRESSION_PATTERN`` from this
module.

``validate_expression_syntax`` is syntax-only by design: referenced
nodes may legitimately not have run (or even exist yet) at save time —
reference *resolution* stays a runtime concern.
"""

from __future__ import annotations

import re

# Matches {{$source.path[0].key}} patterns.
# Path parts allow hyphens (e.g. x-xss-protection in HTTP headers).
EXPRESSION_PATTERN = re.compile(
    r"\{\{\$([a-zA-Z0-9_-]+)((?:\.[a-zA-Z0-9_-]+|\[\d+\])*)\}\}"
)


def validate_expression_syntax(text: str) -> str | None:
    """Return an error message for malformed ``{{…}}`` syntax, or None.

    Plain literals (no braces) are always fine. When braces appear,
    every ``{{…}}`` region must match the resolver grammar — the same
    rule the resolver applies at runtime (where a mismatch silently
    fails to substitute, the #1 authoring trap this check moves to
    save time).
    """
    if "{{" not in text and "}}" not in text:
        return None
    leftover = EXPRESSION_PATTERN.sub("", text)
    if "{{" in leftover or "}}" in leftover:
        return (
            "malformed template expression — expected {{$source.path}} "
            "with no spaces inside the braces"
        )
    return None
