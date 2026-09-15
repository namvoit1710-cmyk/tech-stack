"""SA-2053 (R6) — the liveness value object moved out of the layer4 store so
layer2 use cases can import it without a second copy."""
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.layer1_domain.value_objects.worker_liveness import HEALTHY_STATUSES, parse_ts, age_seconds

NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def test_healthy_statuses_still_admits_blank_and_none():
    """Blank/None stay 'alive' — the palette lists such a node, so the executor
    must resolve it or a user drags in a node dispatch then refuses (split brain)."""
    assert HEALTHY_STATUSES == (None, "", "healthy", "active")


def test_parse_ts_iso_with_offset():
    assert parse_ts("2026-07-15T12:00:00+00:00") == datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def test_parse_ts_z_suffix():
    assert parse_ts("2026-07-15T12:00:00Z") == datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def test_parse_ts_hana_space_form_is_parsed_not_string_compared():
    """SA-2058 regression guard: space 0x20 < 'T' 0x54 at index 10, so a raw-string
    compare sorts a fresh HANA timestamp below an ISO cutoff and calls it stale."""
    assert parse_ts("2026-07-15 12:00:00") == datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def test_parse_ts_naive_is_assumed_utc():
    assert parse_ts("2026-07-15T12:00:00").tzinfo == timezone.utc


def test_parse_ts_blank_sorts_oldest():
    assert parse_ts("") == datetime.min.replace(tzinfo=timezone.utc)


def test_parse_ts_garbage_sorts_oldest():
    assert parse_ts("not-a-timestamp") == datetime.min.replace(tzinfo=timezone.utc)


def test_age_seconds_measures_elapsed_time():
    assert age_seconds((NOW - timedelta(seconds=45)).isoformat(), NOW) == 45.0


def test_age_seconds_is_none_for_a_blank_heartbeat():
    """None means UNKNOWN. `now - datetime.min` is ~6.4e10 s, which renders as
    "last seen 2,000 years ago" for a worker that registered three seconds ago.
    parse_ts's sentinel is safe to ORDER by and unsafe to SUBTRACT."""
    assert age_seconds("", NOW) is None


def test_age_seconds_is_none_for_an_unparseable_heartbeat():
    assert age_seconds("not-a-timestamp", NOW) is None


def test_exactly_one_definition_of_each_in_the_service():
    """RC-6 + RC-12 with teeth. A second definition of 'alive' or a third tolerant
    parser is the condition under which the SA-2058 defect gets reborn."""
    service_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    app_dir = os.path.join(service_root, "app")
    defs_parse, defs_healthy = 0, 0
    for dirpath, _dirs, files in os.walk(app_dir):
        if "__pycache__" in dirpath:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            with open(os.path.join(dirpath, fn), encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("def parse_ts") or line.startswith("def _parse_ts"):
                        defs_parse += 1
                    if line.startswith("HEALTHY_STATUSES =") or line.startswith("_HEALTHY ="):
                        defs_healthy += 1
    assert defs_parse == 1, f"expected 1 parse_ts definition in app/, found {defs_parse}"
    assert defs_healthy == 1, f"expected 1 HEALTHY_STATUSES definition in app/, found {defs_healthy}"
