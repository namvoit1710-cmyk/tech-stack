"""Profile reading + the readiness gate.

The profile is the one place req2tpl is honestly not automatic, so this module's
job is to report that state accurately — never to fill a line in.
"""

from __future__ import annotations

import pytest

from app.layer2_application.services import profile_doc

_READY = """\
[object]
id          = "PIR"
name        = "PIR"
template_id = "PIR_TEST_TEMPLATE"
model_ns    = "pir.model.final"

[structure_map]
S_EINA = ["PurchasingInfoRecord"]
S_EINE = ["PurchasingOrg"]
"""

_STUB = """\
[object]
id          = "PIR"
template_id = ""
model_ns    = "pir.model.final"

[structure_map]
S_EINA = ["PurchasingInfoRecord"]
  # VERIFY: S_SCALES = ["QuantityScale"]   # TIE - you decide
  # VERIFY: S_EINE_TEXT = ["PurchasingOrg"]   # LOW CONFIDENCE
"""


class TestVerifyLines:
    def test_collects_every_line_init_would_not_stand_behind(self):
        lines = profile_doc.verify_lines(_STUB)
        assert len(lines) == 2
        assert lines[0].startswith("S_SCALES")

    def test_a_settled_profile_has_none(self):
        assert profile_doc.verify_lines(_READY) == []


class TestSummarize:
    def test_reports_the_fields_a_reviewer_needs(self):
        summary = profile_doc.summarize(_READY)
        assert summary["template_id"] == "PIR_TEST_TEMPLATE"
        assert summary["object_id"] == "PIR"
        assert summary["model_ns"] == "pir.model.final"
        assert summary["structure_map_count"] == 2
        assert summary["structure_map"]["S_EINA"] == ["PurchasingInfoRecord"]
        assert summary["verify_pending"] == 0

    def test_commented_verify_lines_are_not_counted_as_structure_map(self):
        """They are TOML comments — the whole point is that they are not active."""
        summary = profile_doc.summarize(_STUB)
        assert summary["structure_map_count"] == 1
        assert summary["verify_pending"] == 2

    def test_malformed_toml_fails_readably(self):
        with pytest.raises(ValueError, match="VALIDATION_ERROR: profile is not valid TOML"):
            profile_doc.summarize("[object\nid = ")


class TestReadiness:
    def test_a_settled_profile_is_ready(self):
        ready, problems = profile_doc.readiness(_READY)
        assert ready is True
        assert problems == []

    def test_a_stub_is_blocked_on_the_unset_template_id(self):
        ready, problems = profile_doc.readiness(_STUB)
        assert ready is False
        assert any("template_id" in p for p in problems)

    def test_the_tools_own_change_me_placeholder_blocks(self):
        """`req2tpl init` writes template_id = "CHANGE_ME" (src/main.rs:626). Missing
        this let a stub through reporting ready=true — found on the first real run,
        not by a unit test, which is why the token is asserted literally here."""
        toml = _READY.replace("PIR_TEST_TEMPLATE", "CHANGE_ME")
        ready, problems = profile_doc.readiness(toml)
        assert ready is False
        assert any("template_id" in p for p in problems)

    @pytest.mark.parametrize("placeholder", ["", "TODO", "tbd", "<template_id>", "change_me", "XXX"])
    def test_a_placeholder_template_id_does_not_count_as_named(self, placeholder):
        toml = _READY.replace("PIR_TEST_TEMPLATE", placeholder)
        ready, problems = profile_doc.readiness(toml)
        assert ready is False
        assert any("template_id" in p for p in problems)

    def test_an_empty_structure_map_blocks(self):
        toml = _READY.split("[structure_map]")[0] + "[structure_map]\n"
        ready, problems = profile_doc.readiness(toml)
        assert ready is False
        assert any("structure_map" in p for p in problems)

    def test_outstanding_verify_lines_report_but_do_not_block(self):
        """Blocking on the heuristic is the false-blocker the tool warns about:
        a commented line may be legitimately irrelevant to this object, and only
        the operator can say which."""
        toml = _READY + '\n  # VERIFY: S_SCALES = ["QuantityScale"]\n'
        ready, problems = profile_doc.readiness(toml)
        assert ready is True
        assert problems == []
        assert profile_doc.summarize(toml)["verify_pending"] == 1
