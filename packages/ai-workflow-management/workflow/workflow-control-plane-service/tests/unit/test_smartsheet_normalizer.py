"""Unit tests for the Smartsheet normaliser (smartsheet-event-trigger S3).

Golden sample below is the ⚠️TO-VERIFY seam: replace GOLDEN_EVENTS with a real
``GET /2.0/events`` 200 capture once a System Admin token is available — the
mapping + envelope assertions then run against reality with no other change.
Includes an S3↔S4 integration check so the normaliser + poller are proven wired.
"""

import pytest

from app.layer1_domain.value_objects.event_id import EventId
from app.layer2_application.services.smartsheet_poller.drain import SmartsheetEventsPage
from app.layer2_application.services.smartsheet_poller.normalizer import make_normalizer
from app.layer2_application.services.smartsheet_poller.poller import SmartsheetPoller
from app.layer4_frameworks.persistence.memory.memory_smartsheet_cursor_repo import (
    MemorySmartsheetCursorRepository,
)

pytestmark = pytest.mark.unit

# ⚠️TO-VERIFY: documented Event Reporting shape (uppercase enums). Swap for a real
# 200 capture when a sysadmin token lands.
GOLDEN_EVENTS = [
    {"objectType": "SHEET", "action": "UPDATE", "objectId": 6242348984913796,
     "eventTimestamp": "2026-07-18T10:00:00Z", "userId": 8179063459211140,
     "source": "WEB_APP", "additionalDetails": {"sheetName": "Kruger - Project Schedule"}},
    {"objectType": "ROW", "action": "CREATE", "objectId": 111,
     "eventTimestamp": "2026-07-18T10:01:00Z", "source": "API", "additionalDetails": {}},
    {"objectType": "ATTACHMENT", "action": "LOAD", "objectId": 222,
     "eventTimestamp": "2026-07-18T10:02:00Z"},  # unmapped → filtered
]


def test_maps_known_pair_to_envelope():
    normalize = make_normalizer(tenant_id="acme")
    env = normalize(GOLDEN_EVENTS[0])
    assert env is not None
    assert env["event_topic"] == "smartsheet.sheet.updated"
    assert env["tenant_id"] == "acme"
    assert env["source"] == "smartsheet"
    assert env["occurred_at"] == "2026-07-18T10:00:00Z"
    assert env["payload"] is GOLDEN_EVENTS[0]
    assert set(env) == {"event_id", "event_topic", "tenant_id", "source", "occurred_at", "payload"}


def test_enum_casing_is_absorbed():
    normalize = make_normalizer(tenant_id="acme")
    lower = normalize({"objectType": "sheet", "action": "update", "objectId": 1, "eventTimestamp": "t"})
    upper = normalize({"objectType": "SHEET", "action": "UPDATE", "objectId": 1, "eventTimestamp": "t"})
    assert lower is not None and upper is not None
    assert lower["event_topic"] == upper["event_topic"] == "smartsheet.sheet.updated"
    assert lower["event_id"] == upper["event_id"]  # casing must not fork the dedup key


def test_unmapped_pair_is_filtered():
    normalize = make_normalizer(tenant_id="acme")
    assert normalize(GOLDEN_EVENTS[2]) is None  # ATTACHMENT/LOAD not in the map


def test_event_id_is_stable_and_action_sensitive():
    normalize = make_normalizer(tenant_id="acme")
    e = {"objectType": "SHEET", "action": "UPDATE", "objectId": 5, "eventTimestamp": "t0"}
    assert normalize(e)["event_id"] == normalize(dict(e))["event_id"]  # deterministic
    other = normalize({**e, "action": "DELETE"})
    assert other["event_id"] != normalize(e)["event_id"]  # different action → different id
    # matches the SA-2059 derivation contract
    assert normalize(e)["event_id"] == EventId.derive("smartsheet:sheet:5", "update:t0")


def test_wildcard_action_and_default_topic():
    normalize = make_normalizer(
        tenant_id="acme",
        topic_map={("row", "*"): "smartsheet.row.any"},
        default_topic="smartsheet.other",
    )
    assert normalize({"objectType": "ROW", "action": "DELETE", "objectId": 1, "eventTimestamp": "t"})[
        "event_topic"] == "smartsheet.row.any"
    assert normalize({"objectType": "SHEET", "action": "UPDATE", "objectId": 1, "eventTimestamp": "t"})[
        "event_topic"] == "smartsheet.other"  # falls back to default


def test_golden_page_mapped_and_filtered_counts():
    normalize = make_normalizer(tenant_id="acme")
    out = [normalize(e) for e in GOLDEN_EVENTS]
    mapped = [e for e in out if e is not None]
    assert len(mapped) == 2 and out.count(None) == 1


def test_s3_s4_integration_publishes_normalized_envelopes():
    repo = MemorySmartsheetCursorRepository()

    class _Fetcher:
        def __init__(self, page):
            self._page = page
        def __call__(self, params):
            return self._page

    published: list[dict] = []
    poller = SmartsheetPoller(
        cursor_repo=repo,
        fetch_page=_Fetcher(SmartsheetEventsPage(
            events=GOLDEN_EVENTS, next_stream_position="p2", more_available=False)),
        normalize=make_normalizer(tenant_id="acme"),
        publish=published.append,
        bootstrap_since=lambda: "2026-07-11T00:00:00Z",
        clock=lambda: "t-saved",
    )
    res = poller.run_cycle()

    assert res.drained == 3 and res.published == 2 and res.skipped == 1  # attachment filtered
    assert [e["event_topic"] for e in published] == ["smartsheet.sheet.updated", "smartsheet.row.created"]
    assert all(e["event_id"] for e in published)  # dedup key stamped
    assert repo.get("smartsheet").stream_position == "p2"
