"""Unit tests for the Smartsheet drain loop (smartsheet-event-trigger S2).

Pure pagination orchestration — exercised with a fake fetcher, no live token.
Locks the contract S4's poller depends on: page until moreAvailable clears, resume
vs bootstrap, cursor always advances, and the max_pages backstop defers (never loses).
"""

import pytest

from app.layer2_application.services.smartsheet_poller.drain import (
    SmartsheetEventsPage,
    drain_events,
)

pytestmark = pytest.mark.unit


class _FakeFetcher:
    """Returns queued pages in order; records the params of each call."""

    def __init__(self, pages: list[SmartsheetEventsPage]) -> None:
        self._pages = list(pages)
        self.calls: list[dict] = []

    def __call__(self, params: dict) -> SmartsheetEventsPage:
        self.calls.append(dict(params))
        return self._pages.pop(0)


def test_needs_since_or_stream_position():
    with pytest.raises(ValueError):
        drain_events(_FakeFetcher([]))


def test_bootstrap_single_page():
    f = _FakeFetcher([
        SmartsheetEventsPage(events=[{"id": 1}], next_stream_position="p2", more_available=False),
    ])
    res = drain_events(f, since="2026-07-18T00:00:00Z")
    assert [e["id"] for e in res.events] == [1]
    assert res.stream_position == "p2"
    assert res.pages == 1 and res.truncated is False
    assert f.calls[0] == {"since": "2026-07-18T00:00:00Z"}  # first call bootstraps


def test_resume_paginates_until_more_available_clears():
    f = _FakeFetcher([
        SmartsheetEventsPage(events=[{"id": 1}], next_stream_position="p2", more_available=True),
        SmartsheetEventsPage(events=[{"id": 2}], next_stream_position="p3", more_available=True),
        SmartsheetEventsPage(events=[{"id": 3}], next_stream_position="p4", more_available=False),
    ])
    res = drain_events(f, stream_position="p1")
    assert [e["id"] for e in res.events] == [1, 2, 3]
    assert res.stream_position == "p4"
    assert res.pages == 3
    # first resumes from p1, then follows each nextStreamPosition
    assert f.calls == [{"streamPosition": "p1"}, {"streamPosition": "p2"}, {"streamPosition": "p3"}]


def test_empty_page_still_advances_cursor():
    f = _FakeFetcher([
        SmartsheetEventsPage(events=[], next_stream_position="pX", more_available=False),
    ])
    res = drain_events(f, since="2026-07-18T00:00:00Z")
    assert res.events == []
    assert res.stream_position == "pX"


def test_stream_position_takes_precedence_over_since():
    f = _FakeFetcher([SmartsheetEventsPage(more_available=False, next_stream_position="p2")])
    drain_events(f, since="2026-07-18T00:00:00Z", stream_position="p1")
    assert f.calls[0] == {"streamPosition": "p1"}  # resume beats re-bootstrap


def test_max_pages_truncates_and_defers_without_losing_cursor():
    f = _FakeFetcher([
        SmartsheetEventsPage(events=[{"id": 1}], next_stream_position="p2", more_available=True),
        SmartsheetEventsPage(events=[{"id": 2}], next_stream_position="p3", more_available=True),
        SmartsheetEventsPage(events=[{"id": 3}], next_stream_position="p4", more_available=True),
    ])
    res = drain_events(f, stream_position="p1", max_pages=2)
    assert res.truncated is True
    assert res.pages == 2
    assert res.stream_position == "p3"  # advanced → next cycle resumes here, nothing lost


def test_more_available_but_empty_cursor_stops_instead_of_looping():
    f = _FakeFetcher([
        SmartsheetEventsPage(events=[{"id": 1}], next_stream_position="", more_available=True),
    ])
    res = drain_events(f, stream_position="p1")
    assert [e["id"] for e in res.events] == [1]
    assert res.pages == 1
    assert res.stream_position == "p1"  # no new cursor → keep the last good one
