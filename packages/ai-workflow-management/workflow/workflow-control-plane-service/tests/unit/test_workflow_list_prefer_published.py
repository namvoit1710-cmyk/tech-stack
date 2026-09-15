"""Workflow list — opt-in published-preferred version resolution.

Covers the ``?prefer_published`` enhancement: the SQL window that picks each
logical ID's "current" row, and the use-case wiring that carries the flag into
``list_paged`` (and forces the paged/windowed path, not the undeduped fallback).
"""

from __future__ import annotations

import asyncio

from app.layer4_frameworks.persistence.hana.hana_workflow_repo import HanaWorkflowRepository
from app.layer2_application.features.list_workflows.use_cases.list_workflows_use_case import (
    ListWorkflowsCommand,
    ListWorkflowsUseCase,
)
from app.layer2_application.repositories.workflow_repository_interface import (
    ListWorkflowsFilter,
    ListWorkflowsPage,
)

_DEFAULT_WINDOW = (
    'PARTITION BY "ID" ORDER BY "REVISION" DESC NULLS LAST, "UPDATED_AT" DESC'
)


def test_current_window_default_is_latest_edited():
    # Off (the default): no STATUS preference — identical to the pre-enhancement
    # window so the editor list stays latest-edited (SA-1491).
    assert HanaWorkflowRepository._current_window(False) == _DEFAULT_WINDOW
    assert HanaWorkflowRepository._current_window() == _DEFAULT_WINDOW
    assert "STATUS" not in HanaWorkflowRepository._current_window(False)


def test_current_window_prefer_published_sorts_published_first():
    w = HanaWorkflowRepository._current_window(True)
    assert w == (
        'PARTITION BY "ID" ORDER BY '
        "CASE WHEN \"STATUS\" = 'PUBLISHED' THEN 0 ELSE 1 END ASC, "
        '"REVISION" DESC NULLS LAST, "UPDATED_AT" DESC'
    )
    # The published preference must be the PRIMARY sort key (before REVISION),
    # else a later DRAFT edit would still win.
    assert w.index("PUBLISHED") < w.index("REVISION")


class _RecordingRepo:
    """Minimal repo double that records the filter passed to list_paged."""

    def __init__(self) -> None:
        self.captured: ListWorkflowsFilter | None = None

    def list_paged(self, filters: ListWorkflowsFilter) -> ListWorkflowsPage:
        self.captured = filters
        return ListWorkflowsPage(items=[], total=0)


class _Logger:
    def info(self, *a, **k): ...
    def error(self, *a, **k): ...
    def warning(self, *a, **k): ...


def test_prefer_published_flag_flows_into_list_paged():
    repo = _RecordingRepo()
    uc = ListWorkflowsUseCase(_Logger(), repo)
    # prefer_published alone must be enough to take the paged/windowed path
    # (not the undeduped alist() fallback).
    asyncio.run(uc.execute(ListWorkflowsCommand(prefer_published=True)))
    assert repo.captured is not None, "prefer_published must route to list_paged"
    assert repo.captured.prefer_published is True


def test_default_command_does_not_prefer_published():
    repo = _RecordingRepo()
    uc = ListWorkflowsUseCase(_Logger(), repo)
    # A filtered request (top != 20) still routes to list_paged, but the flag
    # defaults off.
    asyncio.run(uc.execute(ListWorkflowsCommand(top=10)))
    assert repo.captured is not None
    assert repo.captured.prefer_published is False
