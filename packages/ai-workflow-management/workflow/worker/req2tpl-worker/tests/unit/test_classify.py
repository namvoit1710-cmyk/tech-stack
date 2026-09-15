"""Which downloaded workbook is which."""

from __future__ import annotations

import pytest

from app.layer2_application.services import classify as C

REAL = ["PIR_requirement.xlsx", "PIR_fs.xlsx", "PIR_template_referral.xlsx"]


class TestClassify:
    def test_the_real_sample_filenames(self):
        r = C.classify(REAL)
        assert r["req_file"] == "PIR_requirement.xlsx"
        assert r["fs_file"] == "PIR_fs.xlsx"
        assert r["referral"] == "PIR_template_referral.xlsx"
        assert r["has_referral"] is True
        assert r["unmatched"] == []

    def test_underscore_separated_fs_matches(self):
        """`\\bfs\\b` does NOT match "PIR_fs.xlsx" — `_` is a word character, so
        there is no boundary before "fs". Matching is on tokens for this reason."""
        assert C.classify(["PIR_fs.xlsx"])["fs_file"] == "PIR_fs.xlsx"

    @pytest.mark.parametrize("name", ["FS.xlsx", "material_master_fs.xlsx",
                                      "SL-FS-v2.xlsx", "BP functional spec.xlsx"])
    def test_fs_spellings(self, name):
        assert C.classify([name])["fs_file"] == name

    @pytest.mark.parametrize("name", ["PIR_requirement.xlsx", "req.xlsx",
                                      "SL_Requirements_v3.xlsx"])
    def test_requirement_spellings(self, name):
        assert C.classify([name])["req_file"] == name

    def test_referral_is_claimed_before_the_looser_rules(self):
        """"PIR_template_referral.xlsx" carries 'template'; without referral-first
        ordering a later rule could take it."""
        r = C.classify(["PIR_template_referral.xlsx", "PIR_requirement.xlsx", "PIR_fs.xlsx"])
        assert r["referral"] == "PIR_template_referral.xlsx"
        assert r["req_file"] == "PIR_requirement.xlsx"

    def test_one_file_is_never_used_for_two_roles(self):
        r = C.classify(["requirement_fs_combined.xlsx", "PIR_fs.xlsx"])
        assert r["req_file"] != r["fs_file"]

    def test_non_workbooks_are_ignored(self):
        r = C.classify(["screenshot.png", "notes.md", "PIR_requirement.xlsx", "PIR_fs.xlsx"])
        assert r["workbooks"] == ["PIR_requirement.xlsx", "PIR_fs.xlsx"]

    def test_xlsm_counts_as_a_workbook(self):
        assert C.classify(["PIR_fs.xlsm"])["fs_file"] == "PIR_fs.xlsm"

    def test_no_referral_is_reported_not_guessed(self):
        r = C.classify(["PIR_requirement.xlsx", "PIR_fs.xlsx"])
        assert r["referral"] == "" and r["has_referral"] is False

    def test_unrecognised_workbooks_are_reported(self):
        r = C.classify(["PIR_requirement.xlsx", "PIR_fs.xlsx", "mystery.xlsx"])
        assert r["unmatched"] == ["mystery.xlsx"]


class TestRequire:
    def test_passes_when_both_mandatory_inputs_are_found(self):
        C.require(C.classify(REAL), REAL)

    def test_names_the_missing_role_and_what_was_actually_there(self):
        names = ["PIR_requirement.xlsx", "screenshot.png"]
        with pytest.raises(ValueError) as exc:
            C.require(C.classify(names), names)
        msg = str(exc.value)
        assert "VALIDATION_ERROR" in msg and "FS" in msg
        assert "screenshot.png" in msg

    def test_reports_both_when_neither_is_found(self):
        with pytest.raises(ValueError, match="requirement and FS"):
            C.require(C.classify(["mystery.xlsx"]), ["mystery.xlsx"])

    def test_an_empty_ticket_says_none(self):
        with pytest.raises(ValueError, match="none"):
            C.require(C.classify([]), [])
