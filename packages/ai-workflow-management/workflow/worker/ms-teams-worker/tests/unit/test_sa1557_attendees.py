"""SA-1557 — Teams Meeting attendees: accept a list from an upstream node.

Covers _as_email_list normalization (manual SEQUENCE, upstream/expression-resolved
array, CODE JSON-string, CSV, coerce-or-raise), the manual+list merge/de-dupe in the
handler, and the new attendees_list schema field.
"""

from unittest.mock import patch

import pytest

from app import node_types as nt
from app.layer2_application.services.graph_meeting_client import GraphMeetingClient


OAUTH_CRED = {"cred-1": {"type": "json", "config": {
    "tokenUrl": "https://login/oauth2/v2.0/token", "clientId": "cid", "clientSecret": "sec"}}}


def _inputs(**extra):
    base = {
        "credential_ref": "cred-1", "__credentials": OAUTH_CRED,
        "organizer": "o@contoso.com", "subject": "Review",
        "start": "2026-07-10T09:00:00", "end": "2026-07-10T09:30:00",
    }
    base.update(extra)
    return base


def _capture_create():
    seen = {}
    def fake(**kw):
        seen["kw"] = kw
        return {"success": True, "event_id": "e", "join_url": "j", "web_link": "w"}
    return seen, fake


class TestAsEmailListNormalization:
    def test_manual_sequence_strings(self):
        assert nt._as_email_list(["a@x.com", " b@x.com "]) == ["a@x.com", "b@x.com"]

    def test_upstream_resolved_array_of_objects(self):
        v = [{"email": "a@x.com"}, {"address": "b@x.com"}, {"value": "c@x.com"}, {"upn": "d@x.com"}]
        assert nt._as_email_list(v) == ["a@x.com", "b@x.com", "c@x.com", "d@x.com"]

    def test_code_control_json_string_array(self):
        assert nt._as_email_list('["a@x.com", "b@x.com"]') == ["a@x.com", "b@x.com"]

    def test_json_object_array_string(self):
        assert nt._as_email_list('[{"email":"a@x.com"}]') == ["a@x.com"]

    def test_comma_separated_string(self):
        assert nt._as_email_list("a@x.com, b@x.com") == ["a@x.com", "b@x.com"]

    def test_single_string(self):
        assert nt._as_email_list("a@x.com") == ["a@x.com"]

    def test_empty_variants(self):
        assert nt._as_email_list(None) == []
        assert nt._as_email_list("") == []
        assert nt._as_email_list([]) == []
        assert nt._as_email_list([None, ""]) == []

    def test_invalid_json_raises_not_drops(self):
        with pytest.raises(ValueError) as exc:
            nt._as_email_list("[not valid json")
        assert "VALIDATION_ERROR" in str(exc.value)

    def test_non_coercible_row_raises_not_drops(self):
        with pytest.raises(ValueError) as exc:
            nt._as_email_list([{"foo": {"nested": 1}}])
        assert "VALIDATION_ERROR" in str(exc.value)

    def test_non_list_scalar_raises(self):
        with pytest.raises(ValueError):
            nt._as_email_list(123)


class TestHandlerMerge:
    def test_manual_and_list_merged_deduped(self):
        seen, fake = _capture_create()
        with patch.object(GraphMeetingClient, "fetch_token", return_value="t"), \
             patch.object(GraphMeetingClient, "create_event", side_effect=fake):
            nt._meeting_handler(_inputs(
                attendees=["a@x.com", "b@x.com"],
                attendees_list='["B@x.com", "c@x.com"]',  # B@x.com dup (case-insensitive)
            ), {})
        # order preserved, case-insensitive de-dupe keeps first occurrence
        assert seen["kw"]["attendees"] == ["a@x.com", "b@x.com", "c@x.com"]

    def test_list_only_from_upstream_array(self):
        seen, fake = _capture_create()
        with patch.object(GraphMeetingClient, "fetch_token", return_value="t"), \
             patch.object(GraphMeetingClient, "create_event", side_effect=fake):
            # upstream expression resolves to a real list at run time
            nt._meeting_handler(_inputs(attendees_list=["x@x.com", "y@x.com"]), {})
        assert seen["kw"]["attendees"] == ["x@x.com", "y@x.com"]

    def test_no_attendees_is_empty_not_error(self):
        seen, fake = _capture_create()
        with patch.object(GraphMeetingClient, "fetch_token", return_value="t"), \
             patch.object(GraphMeetingClient, "create_event", side_effect=fake):
            nt._meeting_handler(_inputs(), {})
        assert seen["kw"]["attendees"] == []

    def test_bad_attendees_list_raises(self):
        with patch.object(GraphMeetingClient, "fetch_token", return_value="t"), \
             patch.object(GraphMeetingClient, "create_event", return_value={"success": True}):
            with pytest.raises(ValueError) as exc:
                nt._meeting_handler(_inputs(attendees_list="[broken"), {})
        assert "VALIDATION_ERROR" in str(exc.value)


class TestSchema:
    def test_attendees_list_field_present(self):
        keys = {f["key"] for f in nt.MS_TEAMS_MEETING_NODE_DEF.input_schema}
        assert "attendees" in keys and "attendees_list" in keys

    def test_attendees_list_is_code_array(self):
        f = next(f for f in nt.MS_TEAMS_MEETING_NODE_DEF.input_schema if f["key"] == "attendees_list")
        assert f["outputType"] == "array"
        assert f["fieldConfig"]["fieldControl"] == "CodeControl"
