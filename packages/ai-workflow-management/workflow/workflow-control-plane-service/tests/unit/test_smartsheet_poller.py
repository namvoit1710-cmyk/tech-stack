"""Unit tests for the Smartsheet poller cycle (smartsheet-event-trigger S4).

Ties S1 cursor + S2 drain to publish, with normalize/publish injected as fakes —
no live token. Locks: bootstrap vs resume, filter/poison skipping, publish-before-persist
(at-least-once), and the interval loop swallowing per-cycle errors.
"""

import asyncio

import pytest

from app.layer1_domain.entities.smartsheet_stream_cursor import SmartsheetStreamCursor
from app.layer2_application.services.smartsheet_poller.drain import SmartsheetEventsPage
from app.layer2_application.services.smartsheet_poller.poller import SmartsheetPoller
from app.layer4_frameworks.persistence.memory.memory_smartsheet_cursor_repo import (
    MemorySmartsheetCursorRepository,
)

pytestmark = pytest.mark.unit


class _Fetcher:
    def __init__(self, pages):
        self._pages = list(pages)
        self.calls = []

    def __call__(self, params):
        self.calls.append(dict(params))
        return self._pages.pop(0)


def _poller(repo, fetcher, normalize, publish, **kw):
    return SmartsheetPoller(
        cursor_repo=repo, fetch_page=fetcher, normalize=normalize, publish=publish,
        bootstrap_since=lambda: "2026-07-11T00:00:00Z", clock=lambda: "t-saved", **kw,
    )


def test_bootstrap_publishes_all_and_persists_cursor():
    repo = MemorySmartsheetCursorRepository()
    fetcher = _Fetcher([
        SmartsheetEventsPage(events=[{"id": "e1"}, {"id": "e2"}],
                             next_stream_position="p2", more_available=False),
    ])
    published = []
    res = _poller(repo, fetcher, lambda ev: {"event_id": ev["id"]}, published.append).run_cycle()

    assert fetcher.calls[0] == {"since": "2026-07-11T00:00:00Z"}  # no cursor → bootstrap
    assert res.drained == 2 and res.published == 2 and res.skipped == 0
    assert [e["event_id"] for e in published] == ["e1", "e2"]
    saved = repo.get("smartsheet")
    assert saved is not None and saved.stream_position == "p2" and saved.updated_at == "t-saved"


def test_resume_uses_stored_stream_position():
    repo = MemorySmartsheetCursorRepository()
    repo.save(SmartsheetStreamCursor(id="smartsheet", stream_position="p1"))
    fetcher = _Fetcher([SmartsheetEventsPage(more_available=False, next_stream_position="p1")])
    _poller(repo, fetcher, lambda ev: {"event_id": ev["id"]}, [].append).run_cycle()
    assert fetcher.calls[0] == {"streamPosition": "p1"}  # resume, not re-bootstrap


def test_normalize_none_is_filtered_not_published():
    repo = MemorySmartsheetCursorRepository()
    fetcher = _Fetcher([
        SmartsheetEventsPage(events=[{"id": "keep"}, {"id": "skip"}, {"id": "keep2"}],
                             next_stream_position="p2", more_available=False),
    ])
    published = []
    normalize = lambda ev: None if ev["id"] == "skip" else {"event_id": ev["id"]}
    res = _poller(repo, fetcher, normalize, published.append).run_cycle()
    assert res.published == 2 and res.skipped == 1
    assert [e["event_id"] for e in published] == ["keep", "keep2"]


def test_normalize_error_skips_one_event_and_cycle_completes():
    repo = MemorySmartsheetCursorRepository()
    fetcher = _Fetcher([
        SmartsheetEventsPage(events=[{"id": "e1"}, {"id": "bad"}, {"id": "e2"}],
                             next_stream_position="p2", more_available=False),
    ])
    published = []

    def normalize(ev):
        if ev["id"] == "bad":
            raise ValueError("boom")
        return {"event_id": ev["id"]}

    res = _poller(repo, fetcher, normalize, published.append).run_cycle()
    assert res.published == 2 and res.skipped == 1          # poison event skipped
    assert repo.get("smartsheet").stream_position == "p2"    # cursor still advances


def test_publish_failure_does_not_advance_cursor():
    # at-least-once: a broker failure must leave the cursor put so we re-drain.
    repo = MemorySmartsheetCursorRepository()
    fetcher = _Fetcher([
        SmartsheetEventsPage(events=[{"id": "e1"}], next_stream_position="p2", more_available=False),
    ])

    def publish(_env):
        raise RuntimeError("broker down")

    with pytest.raises(RuntimeError):
        _poller(repo, fetcher, lambda ev: {"event_id": ev["id"]}, publish).run_cycle()
    assert repo.get("smartsheet") is None  # never persisted → next cycle retries


def test_run_forever_swallows_cycle_errors_and_continues():
    repo = MemorySmartsheetCursorRepository()
    poller = _poller(repo, _Fetcher([]), lambda ev: None, [].append)

    calls = {"n": 0}

    def fake_cycle():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("transient")

    poller.run_cycle = fake_cycle  # type: ignore[method-assign]

    ticks = {"i": 0}

    def keep_going():
        ticks["i"] += 1
        return ticks["i"] <= 3

    asyncio.run(poller.run_forever(0, should_continue=keep_going))
    assert calls["n"] == 3  # ran 3 cycles despite the 2nd raising
