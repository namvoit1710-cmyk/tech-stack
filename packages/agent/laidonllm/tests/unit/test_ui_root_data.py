"""Tests for the SA-2236 root-level RSC data resolver.

The production symptom this fixes, measured on tenant-1:

    GET /__next._tree.txt?_rsc=<hash>   ->  404 application/json   (per navigation)
    GET /ui/__next._tree.txt            ->  200 text/plain

The security-relevant half of these tests is the negative set: this resolver must never
become a general static-file server at the site root.
"""

from __future__ import annotations

import importlib.util
import os

import pytest

# This test lives OUTSIDE the litellm package on purpose. litellm/proxy/ has an
# __init__.py, so pytest resolves a test placed there as `litellm.proxy.test_*` and
# imports litellm/__init__.py, which does an unguarded `import dotenv` (line 15) and
# blows up in a lightweight test env. --import-mode=importlib does NOT avoid it.
#
# Load the module straight from its file, the way this codebase unit-tests its other
# leaf modules (see litellm/proxy/db/hana/config.py: "stdlib-only imports so it can be
# loaded and unit-tested without importing the full litellm package"). Importing
# `litellm.proxy.ui_root_data` instead drags in the whole package - ~30s and a
# dependency chain this test does not need.
_HERE = os.path.dirname(os.path.abspath(__file__))
_TARGET = os.path.join(_HERE, "..", "..", "litellm", "proxy", "ui_root_data.py")
_SPEC = importlib.util.spec_from_file_location("ui_root_data", os.path.normpath(_TARGET))
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)
resolve_root_next_data = _MOD.resolve_root_next_data


@pytest.fixture()
def bundle(tmp_path):
    """A miniature Admin UI export."""
    out = tmp_path / "out"
    (out / "_next" / "static" / "chunks").mkdir(parents=True)
    (out / "teams").mkdir()

    (out / "login").mkdir()

    (out / "index.html").write_text("<html></html>", encoding="utf-8")
    (out / "index.txt").write_text("ROOT-PAYLOAD", encoding="utf-8")
    (out / "__next._tree.txt").write_text("TREE", encoding="utf-8")
    (out / "__next._full.txt").write_text("FULL", encoding="utf-8")
    (out / "teams" / "__next.!KGRhc2hib2FyZCk.teams.txt").write_text("TEAMS", encoding="utf-8")
    (out / "teams" / "index.html").write_text("<html></html>", encoding="utf-8")
    (out / "teams" / "index.txt").write_text("TEAMS-PAYLOAD", encoding="utf-8")
    (out / "login" / "index.txt").write_text("LOGIN-PAYLOAD", encoding="utf-8")
    (out / "_next" / "static" / "chunks" / "app.js").write_text("//js", encoding="utf-8")

    # A secret that happens to live beside the bundle - must never be reachable.
    (tmp_path / "config.yaml").write_text("master_key: sk-secret", encoding="utf-8")
    return out


# --- positive: the files the client actually asks for -------------------------------


def test_resolves_root_tree_file(bundle):
    got = resolve_root_next_data("/__next._tree.txt", str(bundle))
    assert got is not None
    assert open(got, encoding="utf-8").read() == "TREE"


def test_resolves_other_root_data_files(bundle):
    assert resolve_root_next_data("/__next._full.txt", str(bundle)) is not None


def test_resolves_per_route_data_file(bundle):
    got = resolve_root_next_data("/teams/__next.!KGRhc2hib2FyZCk.teams.txt", str(bundle))
    assert got is not None
    assert open(got, encoding="utf-8").read() == "TEAMS"


# --- shape 2: <route>/index.txt at the site root --------------------------------------
# Missed by PR #1731. Produced by a client-side navigation TO a route, e.g.
# router.push('/login') on logout. Reproduced on the tenant 2026-07-31:
#   GET /login/index.txt -> 404   while  /ui/login/index.txt -> 200


def test_resolves_route_page_payload_at_root(bundle):
    """The exact request that was 404ing on the tenant."""
    got = resolve_root_next_data("/login/index.txt", str(bundle))
    assert got is not None
    assert open(got, encoding="utf-8").read() == "LOGIN-PAYLOAD"


def test_route_page_payload_is_not_login_specific(bundle):
    """Every route was affected, not just login."""
    got = resolve_root_next_data("/teams/index.txt", str(bundle))
    assert got is not None
    assert open(got, encoding="utf-8").read() == "TEAMS-PAYLOAD"


def test_resolves_root_index_payload(bundle):
    got = resolve_root_next_data("/index.txt", str(bundle))
    assert got is not None
    assert open(got, encoding="utf-8").read() == "ROOT-PAYLOAD"


def test_index_html_is_still_not_served(bundle):
    """Widening to index.txt must NOT drag index.html along with it."""
    assert resolve_root_next_data("/index.html", str(bundle)) is None
    assert resolve_root_next_data("/teams/index.html", str(bundle)) is None


def test_index_txt_for_a_nonexistent_route_is_refused(bundle):
    assert resolve_root_next_data("/no-such-route/index.txt", str(bundle)) is None


def test_other_txt_basenames_are_still_refused(bundle):
    """Only `index.txt` and `__next.*` are ours - not every .txt."""
    (bundle / "teams" / "secret.txt").write_text("NOPE", encoding="utf-8")
    assert resolve_root_next_data("/teams/secret.txt", str(bundle)) is None


# --- shape 3: flat /ui/<route>.txt ----------------------------------------------------
# Reported by the operator from the very first report and repeatedly missed by me.
# Fires on an UNAUTHENTICATED load of /ui/ (which bounces to the login route):
#   GET /ui/login.txt -> 404   while the payload lives at login/index.txt


def test_resolves_flat_route_payload_under_ui(bundle):
    """The URL the operator reported: /ui/login.txt."""
    got = resolve_root_next_data("/ui/login.txt", str(bundle))
    assert got is not None
    assert open(got, encoding="utf-8").read() == "LOGIN-PAYLOAD"


def test_flat_form_affects_every_route(bundle):
    got = resolve_root_next_data("/ui/teams.txt", str(bundle))
    assert got is not None
    assert open(got, encoding="utf-8").read() == "TEAMS-PAYLOAD"


def test_flat_form_also_at_root(bundle):
    got = resolve_root_next_data("/login.txt", str(bundle))
    assert got is not None
    assert open(got, encoding="utf-8").read() == "LOGIN-PAYLOAD"


def test_flat_form_for_unknown_route_is_refused(bundle):
    assert resolve_root_next_data("/ui/nope.txt", str(bundle)) is None
    assert resolve_root_next_data("/nope.txt", str(bundle)) is None


def test_flat_form_does_not_serve_non_txt(bundle):
    """Only .txt is rewritten - .html and friends stay out."""
    assert resolve_root_next_data("/ui/login.html", str(bundle)) is None
    assert resolve_root_next_data("/login.html", str(bundle)) is None


def test_flat_form_cannot_escape_via_the_ui_strip(bundle):
    """Stripping the `ui/` prefix must not open a traversal."""
    assert resolve_root_next_data("/ui/../config.txt", str(bundle)) is None
    assert resolve_root_next_data("/ui/../../config.txt", str(bundle)) is None


def test_bare_ui_txt_is_refused(bundle):
    assert resolve_root_next_data("/ui.txt", str(bundle)) is None


# --- negative: everything else must pass through (return None) ----------------------


@pytest.mark.parametrize(
    "path",
    [
        "/",  # site root
        "/index.html",  # a real bundle file, but not RSC data
        "/teams/index.html",  # ditto, nested
        "/_next/static/chunks/app.js",  # assets have their own mount
        "/v1/chat/completions",  # a real API route
        "/health/readiness",  # ditto
        "/ui/__next._tree.txt",  # already served by the /ui mount
        "/__next_not_a_data_file.txt",  # prefix must include the dot
        "/x__next._tree.txt",  # basename must START with __next.
        "",  # empty
    ],
)
def test_passes_through_non_data_paths(bundle, path):
    assert resolve_root_next_data(path, str(bundle)) is None


def test_missing_file_is_not_invented(bundle):
    assert resolve_root_next_data("/__next._nope.txt", str(bundle)) is None


# --- traversal: the part that must not regress -------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/../config.yaml",
        "/__next./../../config.yaml",
        "/teams/../../config.yaml",
        "/....//config.yaml",
    ],
)
def test_traversal_is_refused(bundle, path):
    assert resolve_root_next_data(path, str(bundle)) is None


def test_traversal_cannot_reach_a_data_file_outside_the_bundle(tmp_path):
    """Even a correctly-named file is refused when it resolves outside the bundle."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "index.html").write_text("x", encoding="utf-8")
    outside = tmp_path / "__next._tree.txt"
    outside.write_text("OUTSIDE", encoding="utf-8")

    assert resolve_root_next_data("/../__next._tree.txt", str(out)) is None


def test_sibling_directory_prefix_trap(tmp_path):
    """`/out` must not match `/out-secret` - a plain startswith() check would."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "index.html").write_text("x", encoding="utf-8")
    sneaky = tmp_path / "out-secret"
    sneaky.mkdir()
    (sneaky / "__next._tree.txt").write_text("SECRET", encoding="utf-8")

    assert resolve_root_next_data("/../out-secret/__next._tree.txt", str(out)) is None


def test_empty_ui_path_is_refused(bundle):
    assert resolve_root_next_data("/__next._tree.txt", "") is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
