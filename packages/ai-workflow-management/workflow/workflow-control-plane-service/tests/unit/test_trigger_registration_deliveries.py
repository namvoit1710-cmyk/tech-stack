"""Unit tests for the deliveries list-summary derived columns (SA-2059 UI).

The list endpoint strips the raw payload but derives two small, list-safe
columns from it — ``webhook_event`` and ``issue_key`` — so the FE deliveries
table can show them without a per-row detail fetch. These tests pin that
derivation against the real Jira ``jira:issue_updated`` shape plus the
non-Jira / malformed fallbacks.
"""

from __future__ import annotations

import json

from app.layer1_domain.entities.webhook_registration import WebhookDelivery
from app.layer3_adapters.controllers.restful.v1.trigger_registration_controller import (
    _derive_event_columns,
    _delivery_summary,
)

# Trimmed but faithful to the real Jira Automation webhook body.
_JIRA_BODY = json.dumps({
    "webhookEvent": "jira:issue_updated",
    "issue_event_type_name": "issue_updated",
    "issue": {"id": "235047", "key": "ALSI-697", "fields": {"labels": ["verify"]}},
})


def test_derive_jira_issue_updated():
    cols = _derive_event_columns(_JIRA_BODY)
    assert cols == {"webhook_event": "jira:issue_updated", "issue_key": "ALSI-697"}


def test_derive_prefers_webhookevent_over_issue_event_type_name():
    body = json.dumps({"webhookEvent": "jira:issue_created",
                       "issue_event_type_name": "issue_created",
                       "issue": {"key": "SA-1234"}})
    assert _derive_event_columns(body)["webhook_event"] == "jira:issue_created"


def test_derive_flat_body_fallbacks():
    # Jira Automation custom body: no nested issue object, flat issue_key,
    # and only issue_event_type_name present.
    body = json.dumps({"issue_event_type_name": "issue_generic_event", "issue_key": "SA-9"})
    assert _derive_event_columns(body) == {
        "webhook_event": "issue_generic_event", "issue_key": "SA-9",
    }


def test_derive_non_jira_source_is_blank():
    assert _derive_event_columns(json.dumps({"foo": "bar"})) == {
        "webhook_event": "", "issue_key": "",
    }


def test_derive_malformed_and_empty_payloads():
    assert _derive_event_columns("") == {"webhook_event": "", "issue_key": ""}
    assert _derive_event_columns(None) == {"webhook_event": "", "issue_key": ""}
    assert _derive_event_columns("not-json{") == {"webhook_event": "", "issue_key": ""}
    # Valid JSON but not an object (a bare array) — must not raise.
    assert _derive_event_columns("[]") == {"webhook_event": "", "issue_key": ""}


def test_delivery_summary_strips_payload_and_adds_columns():
    d = WebhookDelivery(
        registration_id="reg-1", outcome="published", status_code=202,
        event_topic="t.jira", event_id="evt-1", detail="", payload=_JIRA_BODY,
    )
    row = _delivery_summary(d)
    # Raw payload must NOT leak into the list row.
    assert "payload" not in row
    # Derived columns present + correct.
    assert row["webhook_event"] == "jira:issue_updated"
    assert row["issue_key"] == "ALSI-697"
    # Existing summary fields still pass through.
    assert row["event_topic"] == "t.jira"
    assert row["outcome"] == "published"
    assert row["status_code"] == 202
