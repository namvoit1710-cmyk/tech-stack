"""SA-1491 (F9 + F10) — the workflow list resolves "current" the same way the
detail view does, and its count/data queries stay consistent.

Bug: ``HanaWorkflowRepository.list_paged`` picked the current row per logical
``ID`` with a PUBLISHED-first window, while ``get_by_id`` / ``_pick_current``
pick REVISION-first (latest-edited). So after editing a PUBLISHED workflow (which
forks a new DRAFT with a higher REVISION), the LIST kept showing the old
published row as "latest" while the DETAIL showed the new draft (F9). Separately,
``list_paged``'s count query was REVISION-first but its data query was
PUBLISHED-first, so the paginated ``total`` could disagree with the returned rows
under a status filter (F10).

Fix: both the count and data queries use ONE REVISION-first window
(``current_window``), matching ``_pick_current``. These tests assert the
generated SQL against the fake DB (the resolution rule itself is covered by
``test_workflow_revision_resolution.py``).
"""
from __future__ import annotations

from types import SimpleNamespace

from app.layer4_frameworks.persistence.hana.hana_workflow_repo import HanaWorkflowRepository

_CURRENT_WINDOW = 'PARTITION BY "ID" ORDER BY "REVISION" DESC NULLS LAST, "UPDATED_AT" DESC'


class _CapturingDb:
    """Records every SQL statement; returns empty result sets."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def execute_query(self, sql: str, params: tuple = ()):
        self.queries.append(sql)
        if "COUNT(" in sql:
            return [{"cnt": 0}]
        return []

    def execute_write(self, sql: str, params: tuple = ()) -> None:
        pass


def _list_all_filters() -> SimpleNamespace:
    """No ``id`` filter → the per-ID window branch (the main list)."""
    return SimpleNamespace(
        eq=None, ne=None, gt=None, ge=None, lt=None, le=None,
        contains=None, orderby=None, id=None, status=None, version=None,
        top=50, skip=0,
    )


def _run() -> tuple[str, str]:
    db = _CapturingDb()
    repo = HanaWorkflowRepository(db=db)
    repo.list_paged(_list_all_filters())
    count_sql = next(q for q in db.queries if "COUNT(" in q)
    data_sql = next(q for q in db.queries if "COUNT(" not in q)
    return count_sql, data_sql


def test_list_current_is_revision_first_not_published_first():
    """F9: the list picks the highest-REVISION row per ID (latest-edited),
    matching get_by_id — it no longer prefers PUBLISHED."""
    _count_sql, data_sql = _run()
    assert "CASE STATUS" not in data_sql, "list must not prefer PUBLISHED over a newer edit"
    assert 'ORDER BY "REVISION" DESC NULLS LAST' in data_sql


def test_list_count_and_data_share_one_ordering():
    """F10: count and data must select the SAME representative row per ID, so the
    paginated total matches the returned rows (incl. under a status filter)."""
    count_sql, data_sql = _run()
    assert _CURRENT_WINDOW in count_sql
    assert _CURRENT_WINDOW in data_sql
