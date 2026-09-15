"""Path confinement for the req2tpl worker.

This worker runs on somebody's laptop and leases tasks from a shared tenant, so
every path a workflow supplies is untrusted input. Nothing outside the workspace
root is ever readable or writable through a node:

    <REQ2TPL_WORKSPACE>/<run_dir>/<relative path>

``run_dir`` is a single slug (no separators). Every other path is resolved under
it and re-checked *after* resolution, so a symlink or a ``..`` that survives
normalisation still cannot escape. Absolute paths are rejected outright rather
than silently reinterpreted — a node that asks for ``C:\\Windows\\...`` has made
a mistake worth reporting, not worth guessing about.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from app.layer4_frameworks.config import settings

# A run_dir is a flat slug: letters, digits, dot, dash, underscore. That covers
# a Jira key (SA-2243), a timestamp, and a hand-typed name, and excludes every
# separator and drive letter.
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_DEFAULT_WORKSPACE = Path.home() / ".req2tpl-worker" / "runs"


class WorkspaceError(ValueError):
    """A path was outside the workspace, or malformed. Always a VALIDATION_ERROR."""

    def __init__(self, message: str) -> None:
        super().__init__(f"VALIDATION_ERROR: {message}")


def workspace_root() -> Path:
    """The one directory this worker may touch. Created on first use."""
    configured = (settings.REQ2TPL_WORKSPACE or "").strip()
    root = Path(configured).expanduser() if configured else _DEFAULT_WORKSPACE
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_run_dir(run_dir: Optional[str], *, create: bool = True) -> Path:
    """Resolve a run-directory slug under the workspace root.

    ``run_dir`` must be a single slug. It is NOT a path: ``a/b``, ``..``,
    ``/etc`` and ``C:\\x`` are all rejected.
    """
    slug = (run_dir or "").strip()
    if not slug:
        raise WorkspaceError("'run_dir' is required")
    if not _SLUG_RE.match(slug):
        raise WorkspaceError(
            f"'run_dir' must be a single name of letters/digits/._- (got {slug!r}); "
            "it is a folder name under the worker's workspace, not a path"
        )
    path = (workspace_root() / slug).resolve()
    _assert_inside(path, workspace_root(), "run_dir")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_in(run: Path, relative: Optional[str], *, label: str = "path") -> Path:
    """Resolve a relative path under an already-resolved run directory.

    Parent directories are created for write targets by the caller; this only
    settles *where* the path is allowed to be.
    """
    rel = (relative or "").strip().replace("\\", "/")
    if not rel:
        raise WorkspaceError(f"'{label}' is required")
    # `os.path.isabs` alone is not enough: Python 3.13 stopped treating a
    # drive-less rooted path ("/etc/passwd") as absolute on Windows, so the
    # leading-separator and drive-letter cases are checked explicitly. The
    # confinement check below would still catch these; this is about refusing
    # them with the accurate reason rather than a confusing one.
    if rel.startswith("/") or os.path.isabs(rel) or re.match(r"^[A-Za-z]:", rel):
        raise WorkspaceError(
            f"'{label}' must be relative to the run directory, not an absolute path (got {relative!r})"
        )
    candidate = (run / rel).resolve()
    _assert_inside(candidate, run, label)
    return candidate


def resolve_existing(run: Path, relative: Optional[str], *, label: str = "path") -> Path:
    """``resolve_in`` plus an existence check, so a missing input fails here with
    a readable message rather than inside the CLI."""
    path = resolve_in(run, relative, label=label)
    if not path.exists():
        raise WorkspaceError(f"'{label}' not found in the run directory: {relative}")
    return path


def rel_to_run(run: Path, path: Path) -> str:
    """Path relative to the run dir, forward-slashed — what the node reports back
    so a downstream node can feed it straight into its own input."""
    try:
        return path.resolve().relative_to(run.resolve()).as_posix()
    except ValueError:
        return path.name


def safe_filename(name: str) -> str:
    """Reduce an attachment filename to a leaf name safe to write.

    Jira filenames are user-supplied. Directory components, drive letters and
    the reserved leaf names are stripped; the result never escapes its folder.
    """
    leaf = os.path.basename((name or "").replace("\\", "/")).strip()
    leaf = re.sub(r'[<>:"|?*\x00-\x1f]', "_", leaf)
    leaf = leaf.strip(". ")
    if not leaf or leaf in (".", ".."):
        raise WorkspaceError(f"attachment filename is unusable: {name!r}")
    return leaf


def _assert_inside(candidate: Path, root: Path, label: str) -> None:
    try:
        candidate.relative_to(root)
    except ValueError:
        raise WorkspaceError(
            f"'{label}' resolves outside the worker workspace ({root}) - refused"
        ) from None
