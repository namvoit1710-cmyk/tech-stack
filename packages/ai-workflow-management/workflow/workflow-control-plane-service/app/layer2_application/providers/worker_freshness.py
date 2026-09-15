"""Worker freshness helper (SA-2052, R5).

A CP-local, tolerant timestamp parser + a freshness predicate, shared by both
palette paths (v2 WorkerNodeProvider.list_summaries and v1
ListNodePaletteUseCase._fetch_worker_nodes) so the freshness rule is defined in
exactly one place and never repeated as a raw-string compare (the SA-2058 defect).
"""

from datetime import datetime, timezone, timedelta


def parse_ts(raw: str) -> datetime:
    """Tolerant ISO-8601 parse (ported from the executor). Blank/unparseable ->
    datetime.min (maximally stale). Handles the HANA space form so it is NOT
    string-compared (SA-2058: space 0x20 < 'T' 0x54 at index 10 sorts a fresh
    HANA ts below an ISO cutoff)."""
    if not raw:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def is_fresh(row: dict, stale_after_seconds: int, now: datetime | None = None) -> bool:
    """True if last_heartbeat is within the window, OR the field is ABSENT (gRPC
    can't report it -> degrade to keep; health filter + R4 sweep still guard). A
    present-but-blank value is measurable -> maximally stale -> not fresh. Absent
    vs blank is distinguished by `"last_heartbeat" in row`, NOT .get() truthiness."""
    if "last_heartbeat" not in row:
        return True
    now = now or datetime.now(timezone.utc)
    return parse_ts(row["last_heartbeat"]) >= now - timedelta(seconds=stale_after_seconds)
