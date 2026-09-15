"""Decide which downloaded workbook is the requirement, the FS, and the referral.

This lives in the worker, not in a workflow ``code`` node, for a concrete reason
measured on a live run: a node's ARRAY output is claim-checked to a CSV file and
an expression can only reach it as ``{{$artifacts.<field>}}``, a file_ref — so a
downstream node cannot read a list of filenames at all. Only SCALARS survive
inline. Classifying here lets ``fetch_inputs`` report three plain strings the
next node can actually use, and makes the rule unit-testable besides.

Deterministic on purpose. The names are conventional, and a wrong guess is
better caught by a person reading the reported filenames than by an LLM being
confident.
"""

from __future__ import annotations

import re
from typing import Dict, List, Sequence

_WORKBOOK_SUFFIXES = (".xlsx", ".xlsm")

# Matching is on TOKENS, not regex word boundaries: `\bfs\b` does not match
# "PIR_fs.xlsx" because `_` is a word character, so there is no boundary before
# "fs" — a trap the real sample filenames exposed on the first live run.
_SPLIT = re.compile(r"[^a-z0-9]+")

# Order matters. Referral is matched FIRST because a name like
# "PIR_template_referral.xlsx" would otherwise be claimed by a looser rule, and
# because the referral is the input whose absence changes the result most.
_RULES: List[tuple[str, Sequence[str]]] = [
    ("referral", ("referral", "referal", "template")),
    ("req_file", ("requirement", "requirements", "req", "reqs")),
    ("fs_file", ("fs", "functional", "spec", "specification")),
]


def _tokens(name: str) -> List[str]:
    return [t for t in _SPLIT.split(name.lower()) if t]


def classify(filenames: Sequence[str]) -> Dict[str, object]:
    """Map filenames onto the three req2tpl inputs.

    Returns the three names (empty string when not found), plus the workbook
    list and any names left over, so a caller can report what it did rather
    than only what it concluded.
    """
    books = [n for n in filenames if str(n).lower().endswith(_WORKBOOK_SUFFIXES)]
    taken: set[str] = set()
    picked: Dict[str, str] = {"referral": "", "req_file": "", "fs_file": ""}

    for slot, keywords in _RULES:
        for keyword in keywords:
            if picked[slot]:
                break
            for name in books:
                if name in taken:
                    continue
                if keyword in _tokens(name):
                    picked[slot] = name
                    taken.add(name)
                    break

    return {
        **picked,
        "has_referral": bool(picked["referral"]),
        "workbooks": books,
        "unmatched": [n for n in books if n not in taken],
    }


def require(result: Dict[str, object], filenames: Sequence[str]) -> None:
    """Fail the node when the two mandatory inputs cannot be identified.

    Raising beats returning a flag: the workflow's edges run on ``success``, so a
    raise stops the run at the node that knows what went wrong, with the list of
    what was actually on the ticket.
    """
    missing = [label for label, key in (("requirement", "req_file"), ("FS", "fs_file"))
               if not result.get(key)]
    if not missing:
        return
    seen = ", ".join(str(n) for n in filenames) or "none"
    raise ValueError(
        "VALIDATION_ERROR: cannot identify the " + " and ".join(missing)
        + " workbook among the ticket's attachments (" + seen + "). "
        "Put 'requirement' / 'fs' in the filename, or run the pipeline nodes "
        "individually with the paths set by hand."
    )
