"""Timestamp coercion for spend-log aggregation (SA-2330).

Stdlib-only imports on purpose so it can be loaded and unit-tested without importing the
full ``litellm`` package - the same pattern as
``management_helpers/email_invite_send_guard.py`` and ``db/hana/config.py``.
"""

from __future__ import annotations

from datetime import datetime

# Shapes seen from the backends this proxy runs against. `fromisoformat` covers all of
# these on 3.11+, so the list is a defensive fallback rather than the primary path.
_FALLBACK_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
)


def coerce_datetime(value: object) -> datetime:
    """Return ``value`` as a ``datetime``, whatever shape the DB driver handed back.

    This replaced a single hardcoded ``datetime.strptime(str(v), "%Y-%m-%dT%H:%M:%S.%fZ")``
    (SA-2330). SAP HANA returns a TIMESTAMP column as a value whose ``str()`` is
    ``'2026-07-30 03:06:56.744658'`` - space separator, no ``T``, no trailing ``Z`` - so
    every row raised and ``GET /spend/logs`` returned 500 for any date range with
    ``summarize`` left at its default.

    The fix is deliberately not "add HANA's format next to the other one": this code
    should not care how a driver renders a timestamp. A value that is already a
    ``datetime`` is passed straight through, which is the common case on both backends.

    Raises ``ValueError`` if the value cannot be interpreted - a genuinely malformed
    timestamp should still fail loudly rather than silently become "now".
    """
    if isinstance(value, datetime):
        return value

    text = str(value).strip()
    # A trailing Z is valid ISO 8601 but predates fromisoformat's support on some versions.
    normalised = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalised)
    except ValueError:
        pass

    for fmt in _FALLBACK_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    raise ValueError(f"unrecognised timestamp value for spend log aggregation: {value!r}")
