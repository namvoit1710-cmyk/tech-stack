"""Tests for litellm/proxy/spend_tracking/timestamp_parsing.py (SA-2330).

The production symptom, measured on tenant-1:

    GET /spend/logs?start_date=..&end_date=..   ->  500
        "/spend/logs Error time data '2026-07-30 03:06:56.744658'
         does not match format '%Y-%m-%dT%H:%M:%S.%fZ'"
    GET /spend/logs?..&summarize=false          ->  200

Only the summarised branch parsed the timestamp, and it assumed a single string shape.

Lives outside the litellm package on purpose: litellm/proxy/ has an __init__.py, so a test
placed there is collected as `litellm.proxy.test_*` and imports litellm/__init__.py, which
does an unguarded `import dotenv` and dies during collection in this lightweight env.
--import-mode=importlib does NOT avoid it. The module under test is stdlib-only, so it
loads by file path with no stubbing.
"""

from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timezone

import pytest

_TARGET = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "..",
    "litellm",
    "proxy",
    "spend_tracking",
    "timestamp_parsing.py",
)
_SPEC = importlib.util.spec_from_file_location("timestamp_parsing", os.path.normpath(_TARGET))
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)
coerce_datetime = _MOD.coerce_datetime


# --- the exact value that produced the 500 -------------------------------------------


def test_hana_rendered_timestamp_is_accepted():
    """The literal string from the production traceback."""
    got = coerce_datetime("2026-07-30 03:06:56.744658")
    assert got == datetime(2026, 7, 30, 3, 6, 56, 744658)


def test_hana_rendered_timestamp_without_microseconds():
    assert coerce_datetime("2026-07-30 03:06:56") == datetime(2026, 7, 30, 3, 6, 56)


# --- the shape the old code assumed must keep working --------------------------------


def test_iso_with_trailing_z_still_accepted():
    got = coerce_datetime("2026-07-30T03:06:56.744658Z")
    assert got.replace(tzinfo=None) == datetime(2026, 7, 30, 3, 6, 56, 744658)
    assert got.utcoffset() == timezone.utc.utcoffset(None)


def test_iso_with_t_separator_no_zone():
    assert coerce_datetime("2026-07-30T03:06:56.744658") == datetime(2026, 7, 30, 3, 6, 56, 744658)


# --- the common case: the driver already gave us a datetime --------------------------


def test_datetime_passes_through_unchanged():
    src = datetime(2026, 7, 30, 3, 6, 56, 744658)
    assert coerce_datetime(src) is src


def test_aware_datetime_passes_through_unchanged():
    src = datetime(2026, 7, 30, 3, 6, 56, tzinfo=timezone.utc)
    assert coerce_datetime(src) is src


# --- a malformed value must still fail loudly ----------------------------------------


@pytest.mark.parametrize("bad", ["", "not-a-timestamp", "30/07/2026 03:06", None])
def test_unparseable_values_raise(bad):
    """Do not let a broken timestamp silently become 'now' - that would corrupt the
    per-date aggregation quietly, which is worse than the 500 this replaced."""
    with pytest.raises(ValueError):
        coerce_datetime(bad)


def test_only_the_date_part_is_used_downstream():
    """The caller does `.date()` on the result, so equal dates across shapes matter."""
    a = coerce_datetime("2026-07-30 23:59:59.999999")
    b = coerce_datetime("2026-07-30T00:00:00.000000")
    assert a.date() == b.date() == datetime(2026, 7, 30).date()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
