"""Reading and gating a req2tpl ``profile.toml``.

The profile is where this pipeline is *not* automatic, and the tool says so
itself: ``template_id`` is never invented, and ``structure_map`` is "genuinely
not derivable" — ``init``'s vote heuristic gets 3 of 6 right on the reference
object, ties on one and is confidently wrong on one. So ``init`` writes the
low-confidence lines commented out with ``# VERIFY:`` and expects a human (or a
reviewer that a human then confirms) to settle them.

This module only *reports* that state. It never fills a line in.
"""

from __future__ import annotations

import re
import tomllib
from typing import Any, Dict, List, Tuple

# ``  # VERIFY: S_SCALES = [...]`` — init's marker for a line it will not stand behind.
_VERIFY_RE = re.compile(r"^\s*#\s*VERIFY:\s*(.+?)\s*$")

# Values that mean "nobody has chosen yet". CHANGE_ME is the literal token
# ``req2tpl init`` writes (src/main.rs:626, with the comment "yours to name; the
# tool never invents one") — missing it let a stub profile through the readiness
# gate reporting ready=true, caught on the first end-to-end run.
_PLACEHOLDERS = {
    "", "change_me", "changeme", "todo", "tbd", "xxx",
    "your_template_id", "<template_id>", "template_id",
}


def verify_lines(toml_text: str) -> List[str]:
    """The ``# VERIFY:`` lines still awaiting a decision, verbatim.

    An empty list does not prove the profile is right — only that ``init`` had
    no low-confidence lines left commented out.
    """
    return [m.group(1) for m in (_VERIFY_RE.match(ln) for ln in toml_text.splitlines()) if m]


def parse(toml_text: str) -> Dict[str, Any]:
    """Parse a profile, raising a node-readable message on malformed TOML."""
    try:
        return tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"VALIDATION_ERROR: profile is not valid TOML: {exc}") from None


def summarize(toml_text: str) -> Dict[str, Any]:
    """The fields a workflow (or a reviewer) wants to see about a profile."""
    doc = parse(toml_text)
    obj = doc.get("object") or {}
    structure_map = doc.get("structure_map") or {}
    pending = verify_lines(toml_text)
    return {
        "object_id": str(obj.get("id") or ""),
        "template_id": str(obj.get("template_id") or ""),
        "model_ns": str(obj.get("model_ns") or ""),
        "structure_map": {str(k): v for k, v in structure_map.items()},
        "structure_map_count": len(structure_map),
        "verify_lines": pending,
        "verify_pending": len(pending),
    }


def readiness(toml_text: str) -> Tuple[bool, List[str]]:
    """Is this profile safe to build from? Returns ``(ready, problems)``.

    Two blocking conditions, both taken from the tool's own manual rather than
    invented here:

    * ``template_id`` unset — "YOURS to name; the tool never invents one".
    * ``structure_map`` empty — the one part that is not derivable.

    Outstanding ``# VERIFY:`` lines are reported but do NOT block: a commented
    line may be legitimately irrelevant to the object, and only the operator can
    say which. Blocking on the heuristic is exactly the false-blocker the tool
    warns about with ``--strict-coverage``.
    """
    problems: List[str] = []
    summary = summarize(toml_text)

    if summary["template_id"].strip().lower() in _PLACEHOLDERS:
        problems.append(
            "[object].template_id is not set — req2tpl never invents one; name it yourself"
        )
    if summary["structure_map_count"] == 0:
        problems.append(
            "[structure_map] is empty — the SAP staging structure -> SMDG table mapping "
            "is the one part req2tpl cannot derive; confirm every line"
        )
    return (not problems), problems
