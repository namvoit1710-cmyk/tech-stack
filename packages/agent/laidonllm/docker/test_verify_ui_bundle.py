"""Tests for the SA-2229 UI bundle consistency gate.

The RED case reproduces the exact deployed failure: `teams/index.html` referencing
`_next/static/chunks/05rzc7f~0d.wh.js`, a chunk the bundle does not contain.
Observed live on smdg-ai-tenant-1-laidonllm 2026-07-30 as:

    ChunkLoadError: Failed to load chunk
      /litellm-asset-prefix/_next/static/chunks/05rzc7f~0d.wh.js from module 964893
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_ui_bundle.py")

# The chunk that actually 404'd in production.
BROKEN_CHUNK = "05rzc7f~0d.wh.js"
GOOD_CHUNK = "0ivj_wax-joap.js"

PAGE_TMPL = (
    "<!DOCTYPE html><html><head>"
    '<link rel="stylesheet" href="/litellm-asset-prefix/_next/static/chunks/site.css"/>'
    "{scripts}"
    "</head><body></body></html>"
)


def _script(chunk: str) -> str:
    return f'<script src="/litellm-asset-prefix/_next/static/chunks/{chunk}"></script>'


def run_gate(bundle: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, SCRIPT, str(bundle)],
        capture_output=True,
        text=True,
    )


def make_bundle(tmp_path, *, emit_broken_chunk: bool):
    """Build a miniature Admin UI bundle.

    `teams` always *references* BROKEN_CHUNK; `emit_broken_chunk` decides whether the
    build actually produced it. False == the SA-2229 mixed-generation bundle.
    """
    bundle = tmp_path / "out"
    chunks = bundle / "_next" / "static" / "chunks"
    chunks.mkdir(parents=True)

    (chunks / "site.css").write_text("body{}", encoding="utf-8")
    (chunks / GOOD_CHUNK).write_text("//ok", encoding="utf-8")
    if emit_broken_chunk:
        (chunks / BROKEN_CHUNK).write_text("//ok", encoding="utf-8")

    (bundle / "index.html").write_text(PAGE_TMPL.format(scripts=_script(GOOD_CHUNK)), encoding="utf-8")

    # The failing route.
    teams = bundle / "teams"
    teams.mkdir()
    teams.joinpath("index.html").write_text(
        PAGE_TMPL.format(scripts=_script(GOOD_CHUNK) + _script(BROKEN_CHUNK)),
        encoding="utf-8",
    )

    # A healthy sibling route, to prove the gate does not blanket-fail.
    users = bundle / "users"
    users.mkdir()
    users.joinpath("index.html").write_text(PAGE_TMPL.format(scripts=_script(GOOD_CHUNK)), encoding="utf-8")

    return bundle


def test_red_dangling_chunk_fails_the_build(tmp_path):
    """SA-2229 reproduction: a referenced chunk the build never emitted must fail."""
    bundle = make_bundle(tmp_path, emit_broken_chunk=False)
    result = run_gate(bundle)

    assert result.returncode == 1, f"gate should fail; stdout={result.stdout}"
    assert BROKEN_CHUNK in result.stderr
    assert "teams/index.html" in result.stderr
    assert "internally inconsistent" in result.stderr


def test_green_consistent_bundle_passes(tmp_path):
    """The same bundle passes once the build emits every chunk it references."""
    bundle = make_bundle(tmp_path, emit_broken_chunk=True)
    result = run_gate(bundle)

    assert result.returncode == 0, f"gate should pass; stderr={result.stderr}"
    assert "OK: UI bundle consistent" in result.stdout
    assert "3 page(s)" in result.stdout


def test_healthy_route_not_blamed(tmp_path):
    """Only the offending page is named - users/ must not be reported."""
    bundle = make_bundle(tmp_path, emit_broken_chunk=False)
    result = run_gate(bundle)

    assert "users/index.html" not in result.stderr


def test_bare_next_prefix_also_checked(tmp_path):
    """Assets referenced without the /litellm-asset-prefix placeholder are checked too."""
    bundle = make_bundle(tmp_path, emit_broken_chunk=True)
    bundle.joinpath("teams", "index.html").write_text(
        '<html><head><script src="/_next/static/chunks/nope.js"></script></head></html>',
        encoding="utf-8",
    )
    result = run_gate(bundle)

    assert result.returncode == 1
    assert "nope.js" in result.stderr


def test_asset_dirs_are_not_scanned_as_pages(tmp_path):
    """HTML living under _next/ is bundler output, not a route - must be skipped."""
    bundle = make_bundle(tmp_path, emit_broken_chunk=True)
    stray = bundle / "_next" / "static" / "stray.html"
    stray.write_text('<script src="/_next/static/chunks/ghost.js"></script>', encoding="utf-8")
    result = run_gate(bundle)

    assert result.returncode == 0, f"stray asset-dir HTML must be ignored; {result.stderr}"


def test_missing_bundle_dir_fails(tmp_path):
    result = run_gate(tmp_path / "does-not-exist")
    assert result.returncode == 1
    assert "not found" in result.stderr


def test_bundle_without_index_fails(tmp_path):
    empty = tmp_path / "out"
    empty.mkdir()
    result = run_gate(empty)
    assert result.returncode == 1
    assert "no index.html" in result.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
