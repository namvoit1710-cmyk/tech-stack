"""Parsing the CLI's output, and failing usefully when it exits non-zero."""

from __future__ import annotations

import pytest

from app.layer2_application.services import req2tpl_cli
from app.layer2_application.services.req2tpl_cli import CliResult, Req2tplError

# The shape the manual documents for `build`.
_BUILD_STDOUT = """\
  fields    : 82
  sections  : 14
  sheets    : 37
  comments  : 298

gates (against the written file, re-unzipped):
  [PASS] trap-bool          1260 boolean cells, all t="b"
  [PASS] trap-number        index cells are real numbers
  [FAIL] field-coverage     QuantityScale 2/9 rows - thin
  [PASS] determinism        two builds byte-identical (97011 bytes)
all gates green.
"""

_MAP_STDOUT = """\
  requirement rows  : 99
  resolved          : 89
  gaps              : 10
  distinct fields   : 79
  uncovered FS PKs  : 0
"""


class TestParseGates:
    def test_reads_every_gate_with_its_status_and_detail(self):
        gates = req2tpl_cli.parse_gates(_BUILD_STDOUT)
        assert [g["name"] for g in gates] == [
            "trap-bool", "trap-number", "field-coverage", "determinism",
        ]
        assert gates[2]["status"] == "FAIL"
        assert "thin" in gates[2]["detail"]

    def test_no_gate_block_yields_nothing(self):
        assert req2tpl_cli.parse_gates(_MAP_STDOUT) == []


class TestParseSummary:
    def test_normalises_labels_to_keys(self):
        summary = req2tpl_cli.parse_summary(_MAP_STDOUT)
        assert summary == {
            "requirement_rows": 99,
            "resolved": 89,
            "gaps": 10,
            "distinct_fields": 79,
            "uncovered_fs_pks": 0,
        }

    def test_ignores_prose_lines(self):
        assert "all gates green." not in req2tpl_cli.parse_summary(_BUILD_STDOUT)

    def test_a_label_the_cli_did_not_print_is_simply_absent(self):
        """Better nothing than a guessed number — these lines are human-facing
        output, not a contract."""
        assert "uncovered_fs_pks" not in req2tpl_cli.parse_summary(_BUILD_STDOUT)


class TestGatesVerdict:
    def test_green_only_when_gates_ran_and_none_failed(self):
        gates = req2tpl_cli.parse_gates(_BUILD_STDOUT)
        verdict = req2tpl_cli.gates_verdict(gates)
        assert verdict["all_gates_green"] is False
        assert verdict["gates_failed"] == ["field-coverage"]
        assert verdict["gates_total"] == 4

    def test_an_empty_gate_list_is_not_green(self):
        """No gates ran is not the same as all gates passed."""
        assert req2tpl_cli.gates_verdict([])["all_gates_green"] is False


class TestRaiseForStatus:
    def test_exit_zero_passes_through(self):
        result = CliResult(argv=["build"], exit_code=0, stdout="", stderr="")
        assert result.raise_for_status("build") is result

    def test_carries_the_cli_message_verbatim(self):
        """The tool's stderr names the cause AND the fix; a paraphrase loses the fix."""
        stderr = "BLOCKING - 2 FS primary key(s) have no template row: add an add_field decision"
        result = CliResult(argv=["map"], exit_code=1, stdout="", stderr=stderr)
        with pytest.raises(Req2tplError) as exc:
            result.raise_for_status("map")
        assert stderr in str(exc.value)
        assert exc.value.error_code == "REQ2TPL_ERROR"

    def test_a_failed_gate_is_named_and_classified(self):
        result = CliResult(
            argv=["build"], exit_code=1, stdout=_BUILD_STDOUT, stderr="gate failed",
            gates=req2tpl_cli.parse_gates(_BUILD_STDOUT),
        )
        with pytest.raises(Req2tplError) as exc:
            result.raise_for_status("build")
        assert "field-coverage" in str(exc.value)
        assert exc.value.error_code == "GATE_FAILED"

    def test_falls_back_to_stdout_when_stderr_is_empty(self):
        result = CliResult(argv=["init"], exit_code=1, stdout="something went wrong", stderr="")
        with pytest.raises(Req2tplError, match="something went wrong"):
            result.raise_for_status("init")


_PAYLOAD_STDOUT = """\
out/PIR_TEMPLATE.xlsx
  4 sheet(s) with data, 111 row(s) - sheets with no data row are omitted
    Template                            1
    TemplateSection                    15
    TemplateSectionField               94
    TemplateIntegrationSetting          1
  transport   : TP
  isOverwrite : false
  tempID      : PIR_IMPORT_TEMPLATE
  wrote out/body.json (exact request body)
"""


class TestParsePayload:
    """`upload payload` prints a different shape: the sheet lines carry no colon,
    and tempID has a colon but a non-numeric value, so the generic summary parser
    sees neither."""

    def test_extracts_the_identity_that_would_reach_the_live_system(self):
        assert req2tpl_cli.parse_payload(_PAYLOAD_STDOUT)["temp_id"] == "PIR_IMPORT_TEMPLATE"

    def test_extracts_the_per_sheet_row_counts(self):
        assert req2tpl_cli.parse_payload(_PAYLOAD_STDOUT)["sheet_rows"] == {
            "Template": 1,
            "TemplateSection": 15,
            "TemplateSectionField": 94,
            "TemplateIntegrationSetting": 1,
        }

    def test_the_generic_summary_parser_really_does_miss_these(self):
        """Guards the reason this function exists — if _SUMMARY_RE ever starts
        catching them, this parser is redundant and should go."""
        assert req2tpl_cli.parse_summary(_PAYLOAD_STDOUT) == {}

    def test_absent_fields_are_none_and_empty_not_invented(self):
        parsed = req2tpl_cli.parse_payload("nothing to see")
        assert parsed == {"temp_id": None, "sheet_rows": {}}


class TestBinaryPath:
    def test_a_configured_path_that_does_not_exist_says_how_to_build_it(self, monkeypatch):
        from app.layer4_frameworks.config import settings

        monkeypatch.setattr(settings, "REQ2TPL_BIN", "/nowhere/req2tpl")
        with pytest.raises(Req2tplError) as exc:
            req2tpl_cli.binary_path()
        assert exc.value.error_code == "CONFIG_ERROR"
        assert "cargo build --release" in str(exc.value)

    def test_falls_back_to_path_lookup(self, monkeypatch, tmp_path):
        from app.layer4_frameworks.config import settings

        monkeypatch.setattr(settings, "REQ2TPL_BIN", "")
        monkeypatch.setattr(req2tpl_cli.shutil, "which", lambda name: str(tmp_path / "req2tpl"))
        assert req2tpl_cli.binary_path().name.startswith("req2tpl")

    def test_absent_everywhere_names_both_ways_to_fix_it(self, monkeypatch):
        from app.layer4_frameworks.config import settings

        monkeypatch.setattr(settings, "REQ2TPL_BIN", "")
        monkeypatch.setattr(req2tpl_cli.shutil, "which", lambda name: None)
        with pytest.raises(Req2tplError, match="REQ2TPL_BIN"):
            req2tpl_cli.binary_path()


class TestReadTextIfExists:
    def test_missing_file_is_none_not_an_error(self, tmp_path):
        assert req2tpl_cli.read_text_if_exists(tmp_path / "gaps.md") is None

    def test_truncates_a_large_report(self, tmp_path):
        path = tmp_path / "gaps.md"
        path.write_text("x" * 5000, encoding="utf-8")
        text = req2tpl_cli.read_text_if_exists(path, max_chars=100)
        assert text.startswith("x" * 100)
        assert "truncated at 100 chars" in text
