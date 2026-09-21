#!/usr/bin/env python3
"""Rename Admin UI assets whose filename collides with OverlayFS whiteout syntax (SA-2229).

Turbopack names chunks from a content hash whose alphabet includes `.`, `~` and `-`.
A hash can therefore produce a basename containing `.wh.` - for example
`05rzc7f~0d.wh.js`. In the OCI/OverlayFS layer format `.wh.` is the *whiteout*
marker ("this path is deleted in this layer"), so such a file does not survive
being baked into an image layer. The exported bundle is correct on the build
host and arrives in the running container with that one file missing.

The result is invisible to every source-tree check and depends on a content hash,
so it appears at random, on one route at a time. Observed in production as
`/ui/teams` -> 404 on `05rzc7f~0d.wh.js` -> ChunkLoadError, while every sibling
route worked.

This script renames the offending files (`.wh.` -> `-wh.`) and rewrites every
reference to them across the bundle's text assets, so the emitted names can never
collide with whiteout syntax. Chunk names are opaque identifiers - only internal
cross-references point at them - so renaming is safe as long as the rewrite is
complete, which `verify_ui_bundle.py` then checks.

Usage:
    python sanitize_ui_bundle.py <bundle-dir>

Exit codes:
    0 - bundle is clean, or was successfully sanitised
    1 - bundle unusable, or a rename/rewrite failed
"""

from __future__ import annotations

import os
import sys

# The OverlayFS whiteout marker. Any basename containing this is unsafe to ship
# inside an image layer.
WHITEOUT = ".wh."
# Rewrite the *leading* dot, never the trailing one. `05rzc7f~0d.wh.js` must become
# `05rzc7f~0d-wh.js` and NOT `05rzc7f~0d.wh-js`: the trailing dot belongs to the file
# extension, and consuming it makes the server answer application/octet-stream, so the
# browser refuses the script and Turbopack reports "can't infer type of chunk from URL".
# That regression shipped once - see test_extension_is_preserved.
REPLACEMENT = "-wh."

# Files whose contents may reference asset filenames.
TEXT_SUFFIXES = (".html", ".js", ".css", ".json", ".txt", ".map")


def find_offenders(bundle: str) -> list[str]:
    """Absolute paths of files whose basename contains the whiteout marker."""
    offenders = []
    for root, _, files in os.walk(bundle):
        for name in files:
            if WHITEOUT in name:
                offenders.append(os.path.join(root, name))
    return sorted(offenders)


def rewrite_references(bundle: str, renames: dict[str, str]) -> int:
    """Replace every old basename with its new one across the bundle's text files."""
    touched = 0
    for root, _, files in os.walk(bundle):
        for name in files:
            if not name.endswith(TEXT_SUFFIXES):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, encoding="utf-8", errors="surrogateescape") as fh:
                    content = fh.read()
            except OSError:
                continue

            updated = content
            for old, new in renames.items():
                if old in updated:
                    updated = updated.replace(old, new)

            if updated != content:
                try:
                    with open(path, "w", encoding="utf-8", errors="surrogateescape") as fh:
                        fh.write(updated)
                except OSError as exc:
                    print(f"FAIL: cannot rewrite {path}: {exc}", file=sys.stderr)
                    raise
                touched += 1
    return touched


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 1

    bundle = os.path.abspath(argv[1])
    if not os.path.isdir(bundle):
        print(f"FAIL: UI bundle directory not found: {bundle}", file=sys.stderr)
        return 1

    offenders = find_offenders(bundle)
    if not offenders:
        print("OK: no whiteout-colliding asset names in bundle.")
        return 0

    renames: dict[str, str] = {}
    for path in offenders:
        directory, old_name = os.path.split(path)

        # Repeat until stable: rewriting one marker can leave another adjacent one
        # (e.g. `a.wh.wh.js`). Bounded so a pathological name cannot spin.
        new_name = old_name
        for _ in range(len(old_name)):
            if WHITEOUT not in new_name:
                break
            new_name = new_name.replace(WHITEOUT, REPLACEMENT)

        if WHITEOUT in new_name:
            print(f"FAIL: could not sanitise {old_name}", file=sys.stderr)
            return 1

        # Hard invariant: the extension decides the served Content-Type. Losing it
        # turns a 404 into an unexecutable application/octet-stream response.
        if os.path.splitext(new_name)[1] != os.path.splitext(old_name)[1]:
            print(
                f"FAIL: sanitising {old_name} -> {new_name} changed the file extension",
                file=sys.stderr,
            )
            return 1

        new_path = os.path.join(directory, new_name)
        if os.path.exists(new_path):
            print(
                f"FAIL: cannot rename {old_name} -> {new_name}: target already exists",
                file=sys.stderr,
            )
            return 1
        try:
            os.replace(path, new_path)
        except OSError as exc:
            print(f"FAIL: cannot rename {path}: {exc}", file=sys.stderr)
            return 1
        renames[old_name] = new_name
        print(f"  renamed {old_name} -> {new_name}")

    touched = rewrite_references(bundle, renames)
    print(f"OK: sanitised {len(renames)} whiteout-colliding asset name(s); rewrote references in {touched} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
