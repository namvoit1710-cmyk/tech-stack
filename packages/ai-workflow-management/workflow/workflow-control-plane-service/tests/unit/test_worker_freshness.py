"""SA-2052 (R5) — CP freshness helper unit tests.

parse_ts is a tolerant ISO-8601 parser (ported from the executor): blank or
unparseable -> datetime.min (maximally stale). It MUST handle the HANA
space-separated form so freshness is a real timestamp compare, NOT a raw-string
compare (the SA-2058 defect: space 0x20 < 'T' 0x54 at index 10 would sort a fresh
HANA ts below an ISO cutoff).

is_fresh distinguishes ABSENT key (gRPC cannot report it -> keep) from a
present-but-blank value (measurable -> maximally stale -> not fresh), via
`"last_heartbeat" in row`, NOT `.get()` truthiness.
"""

import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.layer2_application.providers.worker_freshness import parse_ts, is_fresh


class TestParseTs:
    def test_blank_is_maximally_stale(self):
        assert parse_ts("") == datetime.min.replace(tzinfo=timezone.utc)

    def test_garbage_is_maximally_stale(self):
        assert parse_ts("not-a-timestamp") == datetime.min.replace(tzinfo=timezone.utc)

    def test_iso_with_z_suffix_parses_utc(self):
        assert parse_ts("2026-07-20T12:00:00Z") == datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)

    def test_hana_space_form_parses(self):
        """HANA emits "YYYY-MM-DD HH:MM:SS" (space separator, tz-naive)."""
        assert parse_ts("2026-07-20 12:00:00") == datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)

    def test_tz_naive_is_assumed_utc(self):
        assert parse_ts("2026-07-20T12:00:00") == datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)

    def test_hana_space_form_is_not_below_iso_cutoff(self):
        """SA-2058 regression teeth: a fresh HANA ts must parse ABOVE an older ISO
        cutoff — a raw-string compare would get this wrong (space < 'T' at idx 10)."""
        hana_fresh = "2026-07-20 12:00:00"
        iso_older = "2026-07-20T11:00:00+00:00"
        assert parse_ts(hana_fresh) > parse_ts(iso_older)


class TestIsFresh:
    def test_absent_key_is_kept(self):
        """gRPC transport cannot report last_heartbeat -> degrade to keep."""
        assert is_fresh({"worker_type": "t"}, 120) is True

    def test_empty_row_is_kept(self):
        """The distinction is `"last_heartbeat" in row`, not truthiness."""
        assert is_fresh({}, 120) is True

    def test_blank_value_is_not_fresh(self):
        """A present-but-blank value is measurable -> maximally stale -> not fresh."""
        assert is_fresh({"last_heartbeat": ""}, 120) is False

    def test_fresh_within_window(self):
        now = datetime.now(timezone.utc)
        hb = (now - timedelta(seconds=10)).isoformat()
        assert is_fresh({"last_heartbeat": hb}, 120, now=now) is True

    def test_stale_beyond_window(self):
        now = datetime.now(timezone.utc)
        hb = (now - timedelta(seconds=200)).isoformat()
        assert is_fresh({"last_heartbeat": hb}, 120, now=now) is False

    def test_boundary_exactly_at_window_is_fresh(self):
        """>= cutoff is fresh; age exactly == window is kept (the boundary case)."""
        now = datetime.now(timezone.utc)
        hb = (now - timedelta(seconds=120)).isoformat()
        assert is_fresh({"last_heartbeat": hb}, 120, now=now) is True
