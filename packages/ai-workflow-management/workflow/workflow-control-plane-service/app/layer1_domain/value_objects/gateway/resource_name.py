"""The rule that keeps a gateway app/function name addressable.

A gateway name is not a label. It is simultaneously:

* the **REST route key** — ``/api/v1/gateway/apps/{name}`` and
  ``…/apps/{app_name}/functions/{name}``;
* the **unique index key** — ``IDX_AIW_GW_APPS_NAME`` (unique across ALL rows,
  soft-deleted ones included) and ``IDX_AIW_GW_FUNCS_APP_NAME``;
* an ``NVARCHAR(128)`` column in both tables.

Until 2026-08-12 the only rule was *non-empty*, and the three roles above were
never reconciled. A name containing ``/`` is unreachable by every verb — ``%2F``
is percent-decoded before routing, ``{name}`` matches one path segment, so
``GET``/``PATCH``/``DELETE`` all 404 and the row can never be read, renamed or
removed through the API. tenant-1 carried **42 such rows** written by an OWASP
ZAP scan; they had to be deleted straight out of HANA. A name longer than 128
characters is the same class of bug one step later: it passes every check and
then fails at ``INSERT`` with a 500.

The character set is deliberately narrow rather than "whatever a URL can carry".
Percent-encoding, dots-as-segments and reserved delimiters each have their own
decode-order surprises in some proxy along the way; a name is a technical
identifier, so the safe set costs nothing. All 95 live gateway functions on
tenant-1 pass it, and ``openapi_import._slug`` cannot emit anything outside it.

**Not applied on hydration, on purpose.** ``validate()`` runs from ``create``
and the mutators, never from ``_row_to_entity``. A row that predates this rule
must stay readable and deletable — validating on load would rebuild the exact
trap this closes.
"""
from __future__ import annotations

import re

#: Both NAME columns are NVARCHAR(128) (migrations 043 and 044).
MAX_NAME_LENGTH = 128

#: Must survive one URL path segment untouched, and start with something that
#: cannot be read as a relative-path token (``.``, ``..``).
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def name_error(value: str, *, label: str = "name") -> str | None:
    """Return why ``value`` is unusable as a gateway name, or ``None`` if it is.

    Message-returning rather than raising so ``validate()`` can collect it
    alongside the other invariant violations; callers that mutate a single
    field raise on a non-``None`` result.
    """
    cleaned = (value or "").strip()
    if not cleaned:
        return f"{label} is required"
    if len(cleaned) > MAX_NAME_LENGTH:
        return (
            f"{label} must be at most {MAX_NAME_LENGTH} characters "
            f"(got {len(cleaned)})"
        )
    if not _NAME_RE.match(cleaned):
        return (
            f"{label} '{cleaned}' is not addressable: use letters, digits, "
            "'.', '_' or '-', starting with a letter or digit. The name is "
            "part of the request path, so anything else produces a row that "
            "cannot be read or deleted afterwards."
        )
    return None
