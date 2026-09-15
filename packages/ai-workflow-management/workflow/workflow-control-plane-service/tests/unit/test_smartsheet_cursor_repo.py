"""Unit tests for the Smartsheet stream-cursor store (smartsheet-event-trigger S1).

The poller persists Smartsheet's ``nextStreamPosition`` so it resumes across
restarts. This locks the store contract (get/save/upsert/isolation) that S4's
poller loop depends on — buildable + testable with no live token.
"""

import pytest

from app.layer1_domain.entities.smartsheet_stream_cursor import SmartsheetStreamCursor
from app.layer4_frameworks.persistence.memory.memory_smartsheet_cursor_repo import (
    MemorySmartsheetCursorRepository,
)

pytestmark = pytest.mark.unit


def test_get_missing_returns_none():
    repo = MemorySmartsheetCursorRepository()
    assert repo.get("smartsheet") is None


def test_save_then_get_roundtrip():
    repo = MemorySmartsheetCursorRepository()
    repo.save(SmartsheetStreamCursor(id="smartsheet", stream_position="pos-1", updated_at="t0"))
    got = repo.get("smartsheet")
    assert got is not None
    assert got.stream_position == "pos-1"


def test_save_is_upsert_by_id():
    # A drain cycle advances the cursor; the store must overwrite, not accumulate.
    repo = MemorySmartsheetCursorRepository()
    repo.save(SmartsheetStreamCursor(id="smartsheet", stream_position="pos-1"))
    repo.save(SmartsheetStreamCursor(id="smartsheet", stream_position="pos-2"))
    assert repo.get("smartsheet").stream_position == "pos-2"


def test_cursors_are_isolated_by_id():
    # One cursor per source/tenant — keys must not bleed into each other.
    repo = MemorySmartsheetCursorRepository()
    repo.save(SmartsheetStreamCursor(id="smartsheet:acme", stream_position="a"))
    repo.save(SmartsheetStreamCursor(id="smartsheet:globex", stream_position="g"))
    assert repo.get("smartsheet:acme").stream_position == "a"
    assert repo.get("smartsheet:globex").stream_position == "g"
