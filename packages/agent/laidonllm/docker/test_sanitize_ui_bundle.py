"""Tests for the SA-2229 whiteout-name sanitiser.

Reproduces the production case: a Turbopack content hash yielding `05rzc7f~0d.wh.js`,
whose `.wh.` substring is the OverlayFS whiteout marker, so the file does not survive
being baked into an image layer. Confirmed in the running container 2026-07-30:

    distinct refs              : 180
    DANGLING                   : 1  -> /_next/static/chunks/05rzc7f~0d.wh.js
    dangling with .wh. in name : 1 of 1
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SANITIZE = os.path.join(HERE, "sanitize_ui_bundle.py")
VERIFY = os.path.join(HERE, "verify_ui_bundle.py")

BAD_CHUNK = "05rzc7f~0d.wh.js"
# The leading dot is rewritten, never the trailing one - the trailing dot belongs to
# the `.js` extension. Consuming it shipped once and produced a 200 served as
# application/octet-stream, which the browser still refuses to execute.
SAFE_CHUNK = "05rzc7f~0d-wh.js"
GOOD_CHUNK = "0ivj_wax-joap.js"


def run(script: str, bundle) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, script, str(bundle)], capture_output=True, text=True)


def make_bundle(tmp_path, *, with_bad_name: bool):
    bundle = tmp_path / "out"
    chunks = bundle / "_next" / "static" / "chunks"
    chunks.mkdir(parents=True)

    (chunks / GOOD_CHUNK).write_text("//ok", encoding="utf-8")
    chunk_ref = BAD_CHUNK if with_bad_name else GOOD_CHUNK
    if with_bad_name:
        (chunks / BAD_CHUNK).write_text("//payload", encoding="utf-8")

    # Reference it from HTML *and* from the runtime manifest JS, as Turbopack does.
    (chunks / "turbopack-runtime.js").write_text(
        f'__chunks=["/litellm-asset-prefix/_next/static/chunks/{chunk_ref}"]',
        encoding="utf-8",
    )
    page = (
        "<html><head>"
        f'<script src="/litellm-asset-prefix/_next/static/chunks/{chunk_ref}"></script>'
        '<script src="/litellm-asset-prefix/_next/static/chunks/turbopack-runtime.js"></script>'
        "</head><body></body></html>"
    )
    (bundle / "index.html").write_text(page, encoding="utf-8")
    teams = bundle / "teams"
    teams.mkdir()
    (teams / "index.html").write_text(page, encoding="utf-8")
    return bundle


def test_renames_whiteout_colliding_name(tmp_path):
    bundle = make_bundle(tmp_path, with_bad_name=True)
    result = run(SANITIZE, bundle)

    assert result.returncode == 0, result.stderr
    chunks = bundle / "_next" / "static" / "chunks"
    assert not (chunks / BAD_CHUNK).exists(), "whiteout-colliding file must be gone"
    assert (chunks / SAFE_CHUNK).exists(), "renamed file must exist"
    assert (chunks / SAFE_CHUNK).read_text(encoding="utf-8") == "//payload"


def test_rewrites_references_in_html_and_js(tmp_path):
    bundle = make_bundle(tmp_path, with_bad_name=True)
    run(SANITIZE, bundle)

    html = (bundle / "teams" / "index.html").read_text(encoding="utf-8")
    runtime = (bundle / "_next" / "static" / "chunks" / "turbopack-runtime.js").read_text(encoding="utf-8")
    assert BAD_CHUNK not in html
    assert SAFE_CHUNK in html
    assert BAD_CHUNK not in runtime, "manifest JS references must be rewritten too"
    assert SAFE_CHUNK in runtime


def test_bundle_verifies_clean_after_sanitising(tmp_path):
    """End to end: sanitise, then the consistency gate must pass."""
    bundle = make_bundle(tmp_path, with_bad_name=True)
    assert run(SANITIZE, bundle).returncode == 0
    verify = run(VERIFY, bundle)
    assert verify.returncode == 0, f"gate should pass after sanitising; {verify.stderr}"


def test_no_whiteout_names_remain(tmp_path):
    bundle = make_bundle(tmp_path, with_bad_name=True)
    run(SANITIZE, bundle)
    for root, _, files in os.walk(bundle):
        for name in files:
            assert ".wh." not in name, f"{name} still collides with whiteout syntax"


def test_clean_bundle_is_left_untouched(tmp_path):
    bundle = make_bundle(tmp_path, with_bad_name=False)
    before = {p: os.path.getmtime(os.path.join(r, p)) for r, _, fs in os.walk(bundle) for p in fs}
    result = run(SANITIZE, bundle)

    assert result.returncode == 0
    assert "no whiteout-colliding asset names" in result.stdout
    after = {p: os.path.getmtime(os.path.join(r, p)) for r, _, fs in os.walk(bundle) for p in fs}
    assert before.keys() == after.keys(), "clean bundle must not gain or lose files"


def test_extension_is_preserved(tmp_path):
    """Regression: the rename must not eat the file extension.

    `.wh.js` -> `.wh-js` removed the extension, so the server answered
    application/octet-stream and the browser refused the script with
    "can't infer type of chunk from URL" - a 200 that still breaks the page.
    """
    bundle = make_bundle(tmp_path, with_bad_name=True)
    assert run(SANITIZE, bundle).returncode == 0

    chunks = bundle / "_next" / "static" / "chunks"
    renamed = [p.name for p in chunks.iterdir() if "wh" in p.name and p.name != GOOD_CHUNK]
    assert renamed, "expected a renamed chunk"
    for name in renamed:
        assert name.endswith(".js"), f"{name} lost its .js extension"
        assert ".wh." not in name


@pytest.mark.parametrize(
    "original,expected",
    [
        ("05rzc7f~0d.wh.js", "05rzc7f~0d-wh.js"),
        (".wh.leading.js", "-wh.leading.js"),
        ("a.wh.b.wh.css", "a-wh.b-wh.css"),
        ("double.wh.wh.js", "double-wh-wh.js"),
    ],
)
def test_rename_shapes(tmp_path, original, expected):
    """Every shape keeps its extension and loses every whiteout marker."""
    bundle = tmp_path / "out"
    chunks = bundle / "_next" / "static" / "chunks"
    chunks.mkdir(parents=True)
    (chunks / original).write_text("//x", encoding="utf-8")
    (bundle / "index.html").write_text(f'<script src="/_next/static/chunks/{original}"></script>', encoding="utf-8")

    assert run(SANITIZE, bundle).returncode == 0
    assert (chunks / expected).exists(), f"{original} should become {expected}"
    assert os.path.splitext(expected)[1] == os.path.splitext(original)[1]
    html = (bundle / "index.html").read_text(encoding="utf-8")
    assert expected in html and original not in html


def test_missing_bundle_dir_fails(tmp_path):
    result = run(SANITIZE, tmp_path / "nope")
    assert result.returncode == 1
    assert "not found" in result.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
