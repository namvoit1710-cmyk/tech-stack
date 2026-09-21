"""
Helpers for keeping a runtime-served copy of the packaged Admin UI export in sync
with the UI actually packaged into the running image/build.

`proxy_server.py` can serve the UI from a writable `LITELLM_UI_PATH` instead of the
read-only packaged path (see its "runtime UI path selection" logic), and only ever
populates that path once: if it already has content, it is reused as-is on every
subsequent startup. When `LITELLM_UI_PATH` points at storage that outlives a single
deploy (e.g. a persistent volume), that means a redeploy that changes the UI's
source (new/renamed routes, changed components, etc.) never refreshes it — the
runtime copy keeps serving old, content-hashed `_next/static` chunk files while
`index.html` for a new/changed route references chunks that were never populated
there, producing a browser-side ChunkLoadError/404 for that route while unrelated
routes (whose chunks happen to be unchanged) keep working.

These functions detect that drift by fingerprinting the `_next/static` directory
(content-hashed filenames change whenever the JS/CSS build output changes) and
force a fresh copy when the two builds disagree.
"""

import os
import shutil


def next_static_chunk_names(ui_path: str) -> set:
    """Return the relative filenames under <ui_path>/_next/static.

    Turbopack/webpack content-hashes these filenames, so the returned set is a
    fingerprint of the build: it changes iff the build output changes.
    """
    static_dir = os.path.join(ui_path, "_next", "static")
    names: set = set()
    for root, _, files in os.walk(static_dir):
        for filename in files:
            names.add(os.path.relpath(os.path.join(root, filename), static_dir))
    return names


def runtime_ui_is_stale(packaged_ui_path: str, runtime_ui_path: str) -> bool:
    """Is `runtime_ui_path` populated from a different build than `packaged_ui_path`?

    Returns False (nothing to compare) if the packaged UI has no `_next/static`
    assets to fingerprint, so a missing/invalid packaged build never forces an
    unwanted refresh.
    """
    packaged_chunks = next_static_chunk_names(packaged_ui_path)
    if not packaged_chunks:
        return False
    return packaged_chunks != next_static_chunk_names(runtime_ui_path)


def refresh_runtime_ui(packaged_ui_path: str, runtime_ui_path: str) -> "tuple[bool, str]":
    """Replace the contents of `runtime_ui_path` with a fresh copy of `packaged_ui_path`.

    Returns (success, error_message).
    """
    try:
        if os.path.isdir(runtime_ui_path):
            shutil.rmtree(runtime_ui_path)
        shutil.copytree(packaged_ui_path, runtime_ui_path)
        return True, ""
    except (PermissionError, OSError) as e:
        return False, str(e)
