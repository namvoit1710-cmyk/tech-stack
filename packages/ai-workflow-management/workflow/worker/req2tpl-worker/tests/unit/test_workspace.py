"""Path confinement.

This worker runs on a laptop and takes its instructions from a shared tenant, so
these tests are the security boundary: a workflow must not be able to name a path
outside the workspace, whichever way it spells it.
"""

from __future__ import annotations

import pytest

from app.layer2_application.services.workspace import (
    WorkspaceError,
    rel_to_run,
    resolve_existing,
    resolve_in,
    resolve_run_dir,
    safe_filename,
    workspace_root,
)


class TestRunDir:
    def test_creates_a_slug_directory_under_the_root(self, workspace):
        run = resolve_run_dir("SA-2243")
        assert run.parent == workspace_root()
        assert run.name == "SA-2243"
        assert run.is_dir()

    @pytest.mark.parametrize("bad", ["a/b", "a\\b", "..", "../escape", "/etc", "C:\\Windows", ""])
    def test_rejects_anything_that_is_not_a_flat_slug(self, bad):
        with pytest.raises(WorkspaceError):
            resolve_run_dir(bad)

    def test_create_false_does_not_make_the_directory(self, workspace):
        run = resolve_run_dir("ghost", create=False)
        assert not run.exists()


class TestResolveIn:
    def test_resolves_a_nested_relative_path(self, workspace):
        run = resolve_run_dir("run1")
        assert resolve_in(run, "out/template.xlsx").parent == run / "out"

    def test_backslashes_are_treated_as_separators(self, workspace):
        run = resolve_run_dir("run1")
        assert resolve_in(run, "out\\body.json") == (run / "out" / "body.json")

    @pytest.mark.parametrize("bad", ["../outside.txt", "out/../../outside.txt", "a/../../../x"])
    def test_refuses_to_escape_the_run_directory(self, bad, workspace):
        run = resolve_run_dir("run1")
        with pytest.raises(WorkspaceError, match="outside the worker workspace|relative"):
            resolve_in(run, bad)

    @pytest.mark.parametrize("bad", ["/etc/passwd", "C:/Windows/System32/x", "D:\\secrets.txt"])
    def test_refuses_an_absolute_path(self, bad, workspace):
        run = resolve_run_dir("run1")
        with pytest.raises(WorkspaceError, match="must be relative"):
            resolve_in(run, bad)

    def test_requires_a_value(self, workspace):
        run = resolve_run_dir("run1")
        with pytest.raises(WorkspaceError):
            resolve_in(run, "  ")

    def test_a_sibling_run_directory_is_still_outside(self, workspace):
        """Escaping into ANOTHER run's directory is escaping — runs are the
        isolation unit, not the workspace root."""
        resolve_run_dir("other")
        run = resolve_run_dir("run1")
        with pytest.raises(WorkspaceError):
            resolve_in(run, "../other/template.xlsx")


class TestResolveExisting:
    def test_returns_a_present_file(self, workspace):
        run = resolve_run_dir("run1")
        (run / "req.xlsx").write_bytes(b"x")
        assert resolve_existing(run, "req.xlsx").name == "req.xlsx"

    def test_names_the_missing_input_rather_than_failing_inside_the_cli(self, workspace):
        run = resolve_run_dir("run1")
        with pytest.raises(WorkspaceError, match="not found in the run directory"):
            resolve_existing(run, "nope.xlsx", label="req_file")


class TestSafeFilename:
    @pytest.mark.parametrize(
        "given,expected",
        [
            ("PIR_requirement.xlsx", "PIR_requirement.xlsx"),
            ("../../etc/passwd", "passwd"),
            ("C:\\Users\\x\\evil.xlsx", "evil.xlsx"),
            ("sub/dir/report.md", "report.md"),
            ('we:ird*name?.xlsx', "we_ird_name_.xlsx"),
        ],
    )
    def test_reduces_to_a_safe_leaf(self, given, expected):
        assert safe_filename(given) == expected

    @pytest.mark.parametrize("bad", ["", "   ", "..", "../", "."])
    def test_rejects_a_name_with_nothing_left(self, bad):
        with pytest.raises(WorkspaceError):
            safe_filename(bad)

    def test_a_traversal_filename_lands_inside_the_run_dir(self, workspace):
        """The end-to-end property the download path depends on."""
        run = resolve_run_dir("run1")
        dest = resolve_in(run, safe_filename("../../../../evil.xlsx"))
        assert dest.parent == run


class TestRelToRun:
    def test_reports_a_forward_slashed_relative_path(self, workspace):
        run = resolve_run_dir("run1")
        path = resolve_in(run, "out/template.xlsx")
        assert rel_to_run(run, path) == "out/template.xlsx"
