"""The node registry and the handlers, with the CLI and Jira faked out."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.layer2_application.services import req2tpl_cli
from app.layer2_application.services.req2tpl_cli import CliResult, Req2tplError
from app.layer2_application.services.workspace import WorkspaceError, resolve_run_dir
from app import node_types
from app.node_types import NODE_TYPES

_EXPECTED = {
    "req2tpl_fetch_inputs",
    "req2tpl_survey",
    "req2tpl_init",
    "req2tpl_write_profile",
    "req2tpl_map",
    "req2tpl_build",
    "req2tpl_verify",
    "req2tpl_upload_payload",
    "req2tpl_attach_result",
}


def _by_type(worker_type: str):
    return next(nt for nt in NODE_TYPES if nt.worker_type == worker_type)


class TestRegistry:
    def test_every_expected_node_is_registered_and_no_others(self):
        assert {nt.worker_type for nt in NODE_TYPES} == _EXPECTED

    def test_worker_types_are_unique(self):
        types = [nt.worker_type for nt in NODE_TYPES]
        assert len(types) == len(set(types))

    def test_every_type_shares_the_req2tpl_prefix(self):
        """One `req2tpl_` line in the control plane's credential-affinity map
        covers the whole family — the same trick jira_ uses."""
        assert all(nt.worker_type.startswith("req2tpl_") for nt in NODE_TYPES)

    def test_schemas_are_plain_serialisable_dicts(self):
        for nt in NODE_TYPES:
            assert isinstance(nt.input_schema, list)
            assert all(isinstance(f, dict) and f.get("key") for f in nt.input_schema)
            assert all(isinstance(f, dict) and f.get("key") for f in nt.output_schema)

    def test_every_node_reports_success_and_error_code(self):
        for nt in NODE_TYPES:
            keys = {f["key"] for f in nt.output_schema}
            assert {"success", "error", "error_code"} <= keys

    def test_handlers_are_callable(self):
        assert all(callable(nt.handler) for nt in NODE_TYPES)

    def test_no_design_time_functions_are_declared(self):
        """The FE invokes those over the worker's HTTP endpoint, and a PULL
        worker registers without one — declaring them would render a button that
        cannot work."""
        assert all(nt.functions == [] for nt in NODE_TYPES)

    def test_no_input_field_asks_for_a_secret(self):
        """Auth arrives from the vault via credential_ref, never from the form."""
        banned = ("token", "secret", "password", "apikey", "api_key", "client_secret")
        for nt in NODE_TYPES:
            for field in nt.input_schema:
                assert not any(b in field["key"].lower() for b in banned), (
                    f"{nt.worker_type}.{field['key']} looks like a secret field"
                )


class _FakeCli:
    """Stands in for req2tpl_cli.run, recording argv and optionally writing files."""

    def __init__(self, stdout: str = "", exit_code: int = 0, writes: Dict[str, str] | None = None):
        self.stdout, self.exit_code, self.writes = stdout, exit_code, writes or {}
        self.calls: List[List[str]] = []

    def __call__(self, args, *, cwd: Path, timeout=None) -> CliResult:
        self.calls.append([str(a) for a in args])
        for rel, content in self.writes.items():
            path = Path(cwd) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return CliResult(
            argv=[str(a) for a in args],
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr="" if self.exit_code == 0 else "the CLI said no",
            gates=req2tpl_cli.parse_gates(self.stdout),
            summary=req2tpl_cli.parse_summary(self.stdout),
        )


@pytest.fixture
def fake_cli(monkeypatch):
    def install(stdout="", exit_code=0, writes=None):
        fake = _FakeCli(stdout, exit_code, writes)
        monkeypatch.setattr(node_types.req2tpl_cli, "run", fake)
        return fake
    return install


_BUILD_OK = """\
  fields    : 82
  sections  : 14

gates (against the written file, re-unzipped):
  [PASS] trap-bool          1260 boolean cells
  [PASS] determinism        two builds byte-identical (97011 bytes)
all gates green.
"""


class TestBuildHandler:
    def _seed(self):
        run = resolve_run_dir("SA-1")
        (run / "req.xlsx").write_bytes(b"r")
        (run / "fs.xlsx").write_bytes(b"f")
        (run / "ref.xlsx").write_bytes(b"x")
        (run / "profiles").mkdir()
        (run / "profiles" / "profile.toml").write_text("[object]\n", encoding="utf-8")
        return run

    def test_reports_the_artefact_and_the_gates(self, fake_cli):
        self._seed()
        fake_cli(stdout=_BUILD_OK, writes={"out/template.xlsx": "XLSX"})
        out = _by_type("req2tpl_build").handler(
            {"run_dir": "SA-1", "req_file": "req.xlsx", "fs_file": "fs.xlsx",
             "referral": "ref.xlsx", "profile": "profiles/profile.toml"}, {},
        )
        assert out["success"] is True
        assert out["template_path"] == "out/template.xlsx"
        assert out["all_gates_green"] is True
        assert out["gates_failed"] == []
        assert out["referral_used"] is True
        assert out["template_sha256"] and out["template_bytes"] == 4
        assert out["summary"]["fields"] == 82

    def test_passes_the_referral_through_and_omits_it_when_absent(self, fake_cli):
        self._seed()
        fake = fake_cli(stdout=_BUILD_OK, writes={"out/template.xlsx": "XLSX"})
        _by_type("req2tpl_build").handler(
            {"run_dir": "SA-1", "req_file": "req.xlsx", "fs_file": "fs.xlsx"}, {},
        )
        assert "--referral" not in fake.calls[0]

    def test_optional_switches_reach_the_cli_only_when_on(self, fake_cli):
        self._seed()
        fake = fake_cli(stdout=_BUILD_OK, writes={"out/template.xlsx": "XLSX"})
        _by_type("req2tpl_build").handler(
            {"run_dir": "SA-1", "req_file": "req.xlsx", "fs_file": "fs.xlsx",
             "strict_coverage": True, "allow_open_gaps": False}, {},
        )
        assert "--strict-coverage" in fake.calls[0]
        assert "--allow-open-gaps" not in fake.calls[0]

    def test_a_non_zero_exit_raises_rather_than_returning_a_soft_failure(self, fake_cli):
        self._seed()
        fake_cli(stdout="", exit_code=1)
        with pytest.raises(Req2tplError, match="the CLI said no"):
            _by_type("req2tpl_build").handler(
                {"run_dir": "SA-1", "req_file": "req.xlsx", "fs_file": "fs.xlsx"}, {},
            )

    def test_a_success_that_wrote_no_file_is_still_a_failure(self, fake_cli):
        """Exit 0 is the CLI's claim; the file on disk is the evidence."""
        self._seed()
        fake_cli(stdout=_BUILD_OK, writes={})
        with pytest.raises(Req2tplError, match="wrote no file"):
            _by_type("req2tpl_build").handler(
                {"run_dir": "SA-1", "req_file": "req.xlsx", "fs_file": "fs.xlsx"}, {},
            )

    def test_a_missing_input_is_named_before_the_cli_runs(self, fake_cli):
        self._seed()
        fake = fake_cli(stdout=_BUILD_OK)
        with pytest.raises(WorkspaceError, match="req_file"):
            _by_type("req2tpl_build").handler(
                {"run_dir": "SA-1", "req_file": "nope.xlsx", "fs_file": "fs.xlsx"}, {},
            )
        assert fake.calls == []

    def test_an_escaping_output_path_is_refused(self, fake_cli):
        self._seed()
        fake = fake_cli(stdout=_BUILD_OK)
        with pytest.raises(WorkspaceError):
            _by_type("req2tpl_build").handler(
                {"run_dir": "SA-1", "req_file": "req.xlsx", "fs_file": "fs.xlsx",
                 "template_out": "../../escaped.xlsx"}, {},
            )
        assert fake.calls == []


class TestFanOut:
    """Jira transfers run concurrently. What must NOT change is the reported
    order, and what must NOT be swallowed is a failure."""

    def test_input_order_is_preserved_whatever_finishes_first(self):
        import time

        def slow_then_fast(n):
            time.sleep(0.05 if n == 0 else 0.0)
            return n

        assert node_types._fan_out(slow_then_fast, list(range(6))) == list(range(6))

    def test_an_empty_plan_does_no_work(self):
        assert node_types._fan_out(lambda x: 1 / 0, []) == []

    def test_one_item_takes_the_serial_path(self):
        assert node_types._fan_out(lambda n: n * 2, [21]) == [42]

    def test_a_failure_propagates_rather_than_shortening_the_list(self):
        """A partial upload must fail the node — reporting 2 of 3 files as a
        success is the kind of quiet half-done the seal gate exists to prevent."""
        def boom(n):
            if n == 2:
                raise RuntimeError("transfer failed")
            return n
        with pytest.raises(RuntimeError, match="transfer failed"):
            node_types._fan_out(boom, [0, 1, 2, 3])

    def test_concurrency_setting_of_one_still_works(self, monkeypatch):
        from app.layer4_frameworks.config import settings
        monkeypatch.setattr(settings, "REQ2TPL_JIRA_CONCURRENCY", 1)
        assert node_types._fan_out(lambda n: n + 1, [1, 2, 3]) == [2, 3, 4]

    def test_transfers_actually_overlap(self):
        """The whole point: N transfers must not cost N x latency."""
        import time
        started = time.monotonic()
        node_types._fan_out(lambda n: time.sleep(0.15), list(range(4)))
        elapsed = time.monotonic() - started
        assert elapsed < 0.45, f"4 x 0.15s ran in {elapsed:.2f}s - not overlapping"


class TestDecisions:
    """Decisions are what clear a blocking uncovered FS primary key — the class
    that caused three live import rejections. They reach the worker either as a
    path already on disk or as text from the workflow form, because the person
    making the ruling is usually not at the worker machine."""

    def _seed(self):
        run = resolve_run_dir("SA-9")
        (run / "req.xlsx").write_bytes(b"r")
        (run / "fs.xlsx").write_bytes(b"f")
        (run / "profiles").mkdir()
        (run / "profiles" / "profile.toml").write_text("[object]\n", encoding="utf-8")
        return run

    _ARGS = {"run_dir": "SA-9", "req_file": "req.xlsx", "fs_file": "fs.xlsx"}
    _TOML = '[[decision]]\nid = "X"\nkind = "waive_pk"\ntarget = "Conditions.conditionRecord"\n'

    def test_supplied_text_is_written_and_passed_to_the_cli(self, fake_cli):
        run = self._seed()
        fake = fake_cli()
        _by_type("req2tpl_map").handler({**self._ARGS, "decisions_toml": self._TOML}, {})
        written = run / "profiles" / "decisions.toml"
        assert written.exists() and "waive_pk" in written.read_text(encoding="utf-8")
        assert "--decisions" in fake.calls[0]
        assert str(written) in fake.calls[0]

    def test_build_takes_the_same_route(self, fake_cli):
        run = self._seed()
        fake = fake_cli(stdout=_BUILD_OK, writes={"out/template.xlsx": "X"})
        _by_type("req2tpl_build").handler({**self._ARGS, "decisions_toml": self._TOML}, {})
        assert "--decisions" in fake.calls[0]
        assert (run / "profiles" / "decisions.toml").exists()

    def test_nothing_supplied_means_no_decisions_argument(self, fake_cli):
        """The CLI then blocks on an uncovered PK, which is the correct outcome —
        `allow_open_gaps` is never chosen on the operator's behalf."""
        self._seed()
        fake = fake_cli()
        _by_type("req2tpl_map").handler(dict(self._ARGS), {})
        assert "--decisions" not in fake.calls[0]

    def test_an_unsubstituted_template_token_counts_as_not_supplied(self, fake_cli):
        self._seed()
        fake = fake_cli()
        _by_type("req2tpl_map").handler(
            {**self._ARGS, "decisions_toml": "{{$input.decisions_toml}}"}, {})
        assert "--decisions" not in fake.calls[0]

    def test_a_named_file_already_on_disk_is_used(self, fake_cli):
        run = self._seed()
        (run / "profiles" / "d.toml").write_text(self._TOML, encoding="utf-8")
        fake = fake_cli()
        _by_type("req2tpl_map").handler({**self._ARGS, "decisions": "profiles/d.toml"}, {})
        assert "--decisions" in fake.calls[0]

    def test_a_named_file_that_is_missing_fails_rather_than_being_skipped(self, fake_cli):
        self._seed()
        fake = fake_cli()
        with pytest.raises(WorkspaceError, match="decisions"):
            _by_type("req2tpl_map").handler({**self._ARGS, "decisions": "profiles/gone.toml"}, {})
        assert fake.calls == []

    def test_text_wins_over_a_path(self, fake_cli):
        run = self._seed()
        (run / "profiles" / "decisions.toml").write_text("[[decision]]\nid='old'\n", encoding="utf-8")
        fake_cli()
        _by_type("req2tpl_map").handler({**self._ARGS, "decisions_toml": self._TOML}, {})
        assert "waive_pk" in (run / "profiles" / "decisions.toml").read_text(encoding="utf-8")


class TestWriteProfileHandler:
    _READY = '[object]\ntemplate_id = "PIR_T"\n\n[structure_map]\nS_EINA = ["PurchasingInfoRecord"]\n'
    _STUB = '[object]\ntemplate_id = ""\n\n[structure_map]\n'

    def test_writes_a_settled_profile(self):
        resolve_run_dir("SA-2")
        out = _by_type("req2tpl_write_profile").handler(
            {"run_dir": "SA-2", "profile_toml": self._READY}, {},
        )
        assert out["ready"] is True
        assert out["template_id"] == "PIR_T"
        assert out["profile_path"] == "profiles/profile.toml"
        assert out["sha256"]

    def test_refuses_a_stub_by_default_and_names_both_problems(self):
        resolve_run_dir("SA-2")
        with pytest.raises(ValueError) as exc:
            _by_type("req2tpl_write_profile").handler(
                {"run_dir": "SA-2", "profile_toml": self._STUB}, {},
            )
        assert "template_id" in str(exc.value) and "structure_map" in str(exc.value)

    def test_the_operator_can_override_the_gate(self):
        run = resolve_run_dir("SA-2")
        out = _by_type("req2tpl_write_profile").handler(
            {"run_dir": "SA-2", "profile_toml": self._STUB, "enforce_ready": False}, {},
        )
        assert out["ready"] is False and out["problems"]
        assert (run / "profiles" / "profile.toml").exists()

    def test_malformed_toml_never_reaches_disk(self):
        run = resolve_run_dir("SA-2")
        with pytest.raises(ValueError, match="not valid TOML"):
            _by_type("req2tpl_write_profile").handler(
                {"run_dir": "SA-2", "profile_toml": "[object\n"}, {},
            )
        assert not (run / "profiles" / "profile.toml").exists()


class _FakeJira:
    def __init__(self, attachments: List[Dict[str, Any]] | None = None):
        self.attachments = attachments or []
        self.uploaded: List[tuple] = []
        self.comments: List[str] = []

    def list_attachments(self, issue_key): return self.attachments
    def browse_url(self, issue_key): return f"https://x.atlassian.net/browse/{issue_key}"

    def download_to(self, url, dest: Path, *, max_bytes):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"DATA")
        return {"bytes": 4, "sha256": "deadbeef"}

    def add_attachment(self, issue_key, filename, content, content_type):
        self.uploaded.append((issue_key, filename, len(content), content_type))
        return [{"id": "10001"}]

    def add_comment(self, issue_key, text):
        self.comments.append(text)
        return {"id": "1"}


@pytest.fixture
def fake_jira(monkeypatch):
    def install(attachments=None):
        fake = _FakeJira(attachments)
        monkeypatch.setattr(node_types, "_client", lambda merged: fake)
        return fake
    return install


def _att(filename, aid="1"):
    return {"id": aid, "filename": filename, "size": 4, "mime_type": "application/vnd.ms-excel",
            "created": "", "content_url": f"https://x.atlassian.net/rest/api/3/attachment/content/{aid}",
            "author": ""}


class TestFetchInputsHandler:
    def test_downloads_and_reports_paths_and_the_three_roles_as_scalars(self, fake_jira):
        """The roles must be SCALARS: a node's array output is claim-checked to a
        file the expression layer cannot read, so a downstream node could never
        pick req/fs/referral out of `files` itself."""
        fake_jira([_att("PIR_requirement.xlsx", "1"), _att("PIR_fs.xlsx", "2"),
                   _att("PIR_template_referral.xlsx", "3")])
        out = _by_type("req2tpl_fetch_inputs").handler({"issue_key": "SA-2243"}, {})
        assert out["count"] == 3
        assert out["run_dir"] == "SA-2243"
        assert out["req_file"] == "PIR_requirement.xlsx"
        assert out["fs_file"] == "PIR_fs.xlsx"
        assert out["referral"] == "PIR_template_referral.xlsx"
        assert out["has_referral"] is True
        assert isinstance(out["filenames"], str)
        assert all("path" in f and "sha256" in f for f in out["files"])
        assert not any("content" in f or "base64" in f for f in out["files"])

    def test_a_ticket_without_a_referral_still_runs(self, fake_jira):
        fake_jira([_att("PIR_requirement.xlsx", "1"), _att("PIR_fs.xlsx", "2")])
        out = _by_type("req2tpl_fetch_inputs").handler({"issue_key": "SA-1"}, {})
        assert out["has_referral"] is False and out["referral"] == ""

    def test_a_ticket_missing_the_fs_fails_at_this_node(self, fake_jira):
        """Edges run on `success`, so raising here stops the run at the node that
        knows what is wrong, naming what was actually on the ticket."""
        fake_jira([_att("PIR_requirement.xlsx", "1")])
        with pytest.raises(ValueError, match="cannot identify the FS"):
            _by_type("req2tpl_fetch_inputs").handler({"issue_key": "SA-1"}, {})

    def test_include_filters_and_records_what_it_skipped(self, fake_jira):
        fake_jira([_att("PIR_requirement.xlsx", "1"), _att("PIR_fs.xlsx", "2"),
                   _att("screenshot.png", "3")])
        out = _by_type("req2tpl_fetch_inputs").handler(
            {"issue_key": "SA-1", "include": "PIR_requirement.xlsx, PIR_fs.xlsx"}, {},
        )
        assert out["count"] == 2
        assert out["skipped"][0]["filename"] == "screenshot.png"

    def test_a_traversal_filename_lands_inside_the_run_directory(self, fake_jira, workspace):
        """Jira filenames are user-supplied; this is the path that would let one
        of them write across the machine."""
        fake_jira([_att("PIR_requirement.xlsx", "1"), _att("PIR_fs.xlsx", "2"),
                   _att("../../../../evil.xlsx", "3")])
        out = _by_type("req2tpl_fetch_inputs").handler({"issue_key": "SA-1"}, {})
        assert "evil.xlsx" in [f["path"] for f in out["files"]]
        assert (workspace / "SA-1" / "evil.xlsx").exists()
        assert not (workspace.parent / "evil.xlsx").exists()

    def test_an_issue_with_no_attachments_says_what_is_needed(self, fake_jira):
        fake_jira([])
        with pytest.raises(ValueError, match="no attachments"):
            _by_type("req2tpl_fetch_inputs").handler({"issue_key": "SA-1"}, {})

    def test_a_rerun_reports_the_roles_even_though_it_downloads_nothing(self, fake_jira):
        """Classification reads the run directory, not just this call's downloads."""
        fake_jira([_att("PIR_requirement.xlsx", "1"), _att("PIR_fs.xlsx", "2")])
        _by_type("req2tpl_fetch_inputs").handler({"issue_key": "SA-1"}, {})
        again = _by_type("req2tpl_fetch_inputs").handler({"issue_key": "SA-1"}, {})
        assert again["count"] == 0
        assert again["skipped"][0]["reason"].startswith("already present")
        assert again["req_file"] == "PIR_requirement.xlsx"
        assert again["fs_file"] == "PIR_fs.xlsx"

    def test_run_dir_defaults_to_a_slug_of_the_issue_key(self, fake_jira):
        fake_jira([_att("PIR_requirement.xlsx"), _att("PIR_fs.xlsx", "2")])
        out = _by_type("req2tpl_fetch_inputs").handler({"issue_key": "SA-2243"}, {})
        assert out["run_dir"] == "SA-2243"


class TestAttachResultHandler:
    def test_uploads_each_file_and_adds_the_comment(self, fake_jira):
        run = resolve_run_dir("SA-3")
        (run / "out").mkdir()
        (run / "out" / "template.xlsx").write_bytes(b"XLSXDATA")
        (run / "out" / "gaps.md").write_text("none", encoding="utf-8")
        fake = fake_jira()

        out = _by_type("req2tpl_attach_result").handler(
            {"issue_key": "SA-3", "run_dir": "SA-3",
             "files": "out/template.xlsx, out/gaps.md", "comment": "built + gated"}, {},
        )
        assert out["count"] == 2
        # Uploads run concurrently, so the WIRE order is not guaranteed — only
        # every file going up, and the REPORTED order matching the input.
        assert {u[1] for u in fake.uploaded} == {"template.xlsx", "gaps.md"}
        assert [a["filename"] for a in out["attached"]] == ["template.xlsx", "gaps.md"]
        assert out["comment_added"] is True
        assert fake.comments == ["built + gated"]
        assert out["attached"][0]["attachment_id"] == "10001"

    def test_a_missing_file_fails_before_anything_is_uploaded(self, fake_jira):
        resolve_run_dir("SA-3")
        fake = fake_jira()
        with pytest.raises(WorkspaceError, match="not found"):
            _by_type("req2tpl_attach_result").handler(
                {"issue_key": "SA-3", "run_dir": "SA-3", "files": "out/nope.xlsx"}, {},
            )
        assert fake.uploaded == []

    def test_no_comment_means_no_comment(self, fake_jira):
        run = resolve_run_dir("SA-3")
        (run / "t.xlsx").write_bytes(b"x")
        fake = fake_jira()
        out = _by_type("req2tpl_attach_result").handler(
            {"issue_key": "SA-3", "run_dir": "SA-3", "files": "t.xlsx"}, {},
        )
        assert out["comment_added"] is False
        assert fake.comments == []


class TestInitHandler:
    _STUB = '[object]\nid = "PIR"\ntemplate_id = ""\n\n[structure_map]\nS_EINA = ["PurchasingInfoRecord"]\n  # VERIFY: S_SCALES = ["QuantityScale"]\n'

    def test_surfaces_the_lines_awaiting_confirmation(self, fake_cli):
        run = resolve_run_dir("SA-4")
        (run / "req.xlsx").write_bytes(b"r")
        (run / "fs.xlsx").write_bytes(b"f")
        fake_cli(writes={"profiles/profile.toml": self._STUB})

        out = _by_type("req2tpl_init").handler(
            {"run_dir": "SA-4", "req_file": "req.xlsx", "fs_file": "fs.xlsx"}, {},
        )
        assert out["verify_pending"] == 1
        assert out["ready"] is False
        assert any("template_id" in p for p in out["problems"])
        assert out["profile_toml"].startswith("[object]")
        assert out["structure_map_count"] == 1
