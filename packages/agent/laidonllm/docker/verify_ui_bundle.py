#!/usr/bin/env python3
"""Build-time consistency gate for the packaged Admin UI bundle (SA-2229).

Every asset URL referenced by an exported HTML page MUST exist on disk in the same
bundle. A bundle where `teams/index.html` references a `_next/static` chunk that the
build never emitted is *internally inconsistent*: the page returns 200, the chunk
returns a 404 whose JSON body the browser refuses to execute under strict MIME
checking, and the route dies with `ChunkLoadError` while its sibling routes work.

That is SA-2229. It shipped because nothing verified the bundle at build time.
This script turns that silent, route-specific runtime failure into a red build.

Usage:
    python verify_ui_bundle.py <bundle-dir>

Exit codes:
    0 - every referenced asset resolves
    1 - at least one reference is dangling (or the bundle is unusable)
"""

from __future__ import annotations

import os
import re
import sys
from collections import defaultdict

# Matches the asset URLs Next.js emits into exported HTML, with or without the
# `/litellm-asset-prefix` placeholder that the proxy rewrites at serve time.
ASSET_RE = re.compile(r"""(?:/litellm-asset-prefix)?(/_next/static/[A-Za-z0-9_.~\-/]+?\.(?:js|css))""")

# Bundle-relative directories that hold assets rather than routable pages.
SKIP_DIRS = {"_next", "litellm-asset-prefix", "assets"}


def iter_html(bundle: str):
    """Yield every exported HTML page in the bundle."""
    for root, dirs, files in os.walk(bundle):
        rel_root = os.path.relpath(root, bundle)
        first = "" if rel_root == "." else rel_root.split(os.sep)[0]
        if first in SKIP_DIRS:
            dirs[:] = []
            continue
        for name in files:
            if name.endswith(".html"):
                yield os.path.join(root, name)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 1

    bundle = os.path.abspath(argv[1])
    if not os.path.isdir(bundle):
        print(f"FAIL: UI bundle directory not found: {bundle}", file=sys.stderr)
        return 1

    if not os.path.isfile(os.path.join(bundle, "index.html")):
        print(f"FAIL: no index.html in bundle: {bundle}", file=sys.stderr)
        return 1

    pages = 0
    refs = 0
    # dangling asset -> the pages that reference it
    dangling: dict[str, list[str]] = defaultdict(list)

    for page in iter_html(bundle):
        pages += 1
        try:
            with open(page, "r", encoding="utf-8", errors="ignore") as fh:
                html = fh.read()
        except OSError as exc:
            print(f"FAIL: cannot read {page}: {exc}", file=sys.stderr)
            return 1

        rel_page = os.path.relpath(page, bundle).replace(os.sep, "/")
        for asset in sorted(set(ASSET_RE.findall(html))):
            refs += 1
            on_disk = os.path.join(bundle, asset.lstrip("/").replace("/", os.sep))
            if not os.path.isfile(on_disk):
                dangling[asset].append(rel_page)

    if pages == 0:
        print(f"FAIL: no HTML pages found in bundle: {bundle}", file=sys.stderr)
        return 1

    if dangling:
        print(
            f"FAIL: UI bundle is internally inconsistent - {len(dangling)} referenced asset(s) missing from {bundle}",
            file=sys.stderr,
        )
        print(
            "\nThis is the SA-2229 failure mode: the route returns 200, the missing "
            "chunk returns a JSON 404, and the browser raises ChunkLoadError.\n",
            file=sys.stderr,
        )
        for asset in sorted(dangling):
            pages_listed = ", ".join(sorted(dangling[asset])[:5])
            more = len(dangling[asset]) - 5
            if more > 0:
                pages_listed += f" (+{more} more)"
            print(f"  MISSING {asset}", file=sys.stderr)
            print(f"     referenced by: {pages_listed}", file=sys.stderr)
        print(
            "\nThe HTML and the _next/static chunk set come from different builds. "
            "Rebuild the UI from a clean output directory.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: UI bundle consistent - {pages} page(s), {refs} asset reference(s), 0 missing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
