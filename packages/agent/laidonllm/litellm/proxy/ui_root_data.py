"""Serve the Admin UI's RSC data files when the client asks for them at the site root.

SA-2236. `ui/litellm-dashboard/next.config.mjs` sets `basePath: ""` while the export is
mounted at `/ui`. Static assets escape the mismatch because they resolve through the
separate `assetPrefix`, which the proxy already mounts at the site root (`/_next`) as
well as under the prefix. RSC data files resolve through `basePath`, so the client asks
for them at the root, where nothing is mounted:

    GET /__next._tree.txt?_rsc=<hash>   ->  404 application/json

The file exists and is served correctly one level down (`/ui/__next._tree.txt` -> 200).
This module bridges that gap for exactly those filenames - the same thing the existing
root-level `/_next` mount does for assets, applied to the data files.

Setting `basePath: "/ui"` was tried instead and reverted: the UI already emits hrefs
containing `/ui`, so Next double-prefixed them and every in-app link broke
(`/ui/ui/users` -> 404). See SA-2236 Amendment 3. This approach changes no URLs.

Why a middleware and not a route: `proxy_server.py` mounts the UI around line 1738 of a
~16k-line module, with dozens of endpoints declared *after* that point. A catch-all
`@app.get("/{path:path}")` registered there would shadow all of them. A middleware
inspects the path and passes everything it does not own straight through.
"""

from __future__ import annotations

import os
from typing import Optional

# The client asks for its RSC payloads in TWO shapes. Both are owned by this module.
#
#   1. `__next.<something>.txt` - segment/tree/head/index payloads requested at the site
#      root. Example: /__next._tree.txt
#   2. `<route>/index.txt`      - the page payload for a route, also at the site root,
#      because `trailingSlash: true` makes the client derive `<route>/` + `index.txt`.
#      Example: /login/index.txt
#
# Shape 2 was missed on the first pass (PR #1731 shipped only shape 1). The verification
# there exercised sidebar clicks between already-authenticated routes, which use shape 1
# only. Shape 2 appears on a client-side navigation TO a route - notably
# `router.push('/login')` on logout or session expiry - and 404s for EVERY route:
#     /login/index.txt  404      while  /ui/login/index.txt  200
#     /teams/index.txt  404      while  /ui/teams/index.txt  200
DATA_PREFIX = "__next."
INDEX_PAYLOAD = "index.txt"


UI_MOUNT = "ui/"


def _is_rsc_payload(basename: str) -> bool:
    """True for the payload filename shapes that map 1:1 into the bundle."""
    return basename.startswith(DATA_PREFIX) or basename == INDEX_PAYLOAD


def _flat_route_payload(rel: str) -> Optional[str]:
    """Map the FLAT page-payload form to its nested file - shape 3.

    The client sometimes derives ``<route>.txt`` instead of ``<route>/index.txt``:

        GET /ui/login.txt   ->  bundle has login/index.txt, not login.txt   -> 404

    Observed on an unauthenticated load of ``/ui/`` (which bounces to the login route).
    The ``/ui`` prefix is the mount point, not part of the bundle, so strip it first.

    Returns a bundle-relative path, or None when the request is not this shape.
    """
    if not rel.endswith(".txt"):
        return None
    stem = rel[: -len(".txt")]
    if stem.startswith(UI_MOUNT):
        stem = stem[len(UI_MOUNT) :]
    if not stem or stem == "ui":
        return None
    return stem + "/" + INDEX_PAYLOAD


def resolve_root_next_data(url_path: str, ui_path: str) -> Optional[str]:
    """Map a root-level RSC data URL to a file inside the UI bundle.

    Returns an absolute path to an existing file, or None when the request is not one
    of ours (the caller must then let normal routing continue).

    Deliberately strict - it must never turn into a general static-file server at the
    site root:
      * the BASENAME must be an RSC payload name (see ``_is_rsc_payload``)
      * the resolved path must stay inside ``ui_path`` (traversal guard)
      * the target must already exist as a regular file

    Accepting ``index.txt`` widens nothing: every such file already sits inside the
    bundle and is already public under ``/ui/``. Only the path it answers on changes.
    ``index.html`` and every other basename stay untouched.
    """
    if not url_path or not ui_path:
        return None

    rel = url_path.lstrip("/")
    if not rel:
        return None

    # Bundle-relative candidates, most literal first. The flat fallback is only reached
    # when the literal form is not one of ours or does not exist on disk, so a real file
    # is never shadowed by the rewrite.
    candidates = []
    if _is_rsc_payload(os.path.basename(rel)):
        candidates.append(rel)
    flat = _flat_route_payload(rel)
    if flat:
        candidates.append(flat)
    if not candidates:
        return None

    root = os.path.abspath(ui_path)
    for cand in candidates:
        target = os.path.abspath(os.path.join(root, cand.replace("/", os.sep)))

        # Traversal guard: os.path.commonpath avoids the `/foo` vs `/foobar` prefix trap
        # that a plain startswith() check would let through.
        try:
            if os.path.commonpath([root, target]) != root:
                continue
        except ValueError:
            # Different drives on Windows - definitely outside the bundle.
            continue

        if os.path.isfile(target):
            return target

    return None
