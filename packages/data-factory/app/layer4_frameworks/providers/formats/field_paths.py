"""Turn a mass-upload banner into the ``SECTION.fieldName`` paths rules speak.

The rules configured on a template are written against a field path --
``GENERALDATA.baseUnit`` -- and the error the frontend renders is keyed by that
path:

    {"path": "GENERALDATA.baseUnit", "ruleId": "LENGTH_40", ...}

A mass-upload workbook does not carry that path in one place. It carries the two
halves in its banner rows, with the section written once over a merged range and
the column label underneath:

    row 1:  ['3', 'Article', '', '', '', '', 'General data', '',            ...]
    row 2:  ['ItemID (Integer)', 'Article (String)', ...,  'Base Unit (String)']

So the section for a column is the nearest non-empty cell at or to its left --
a merged cell reads as its value in the first column and blank in the rest --
and the field name is the label with its type hint removed and camel-cased.

That last step is a heuristic, and it is honest about being one. ``Base Unit``
and ``Article Type`` round-trip exactly; ``Haz. Matl No.`` yields ``hazMatlNo``,
which is a guess. Callers that know the real technical names pass ``overrides``
and those win outright. The heuristic exists so a template nobody has mapped yet
still produces usable paths, not so it can be trusted blindly.
"""

import re
from typing import Dict, List, Optional, Sequence

#: ``Base Unit (String)`` -> drop the trailing type hint.
_TYPE_HINT = re.compile(r"\s*\([^)]*\)\s*$")
#: Split a label into words on anything that is not a letter or a digit.
_WORDS = re.compile(r"[^0-9A-Za-z]+")


def normalize_section(text: Optional[str]) -> str:
    """``General data`` -> ``GENERALDATA``, the form the rules are written in."""
    if text is None:
        return ""
    return re.sub(r"[^0-9A-Za-z]+", "", str(text)).upper()


def field_name_from_label(label: Optional[str]) -> str:
    """``Base Unit (String)`` -> ``baseUnit``.

    Internal capitals are kept, so ``ItemID (Integer)`` gives ``itemID`` rather
    than ``itemId`` -- the workbook's own casing is better evidence than any
    rule we could invent for re-capitalising it.
    """
    if label is None:
        return ""
    stripped = _TYPE_HINT.sub("", str(label)).strip()
    words = [w for w in _WORDS.split(stripped) if w]
    if not words:
        return ""
    head, *rest = words
    head = head[:1].lower() + head[1:]
    return head + "".join(w[:1].upper() + w[1:] for w in rest)


def spread_sections(section_row: Sequence[Optional[str]], width: int) -> List[str]:
    """Forward-fill a banner row so every column knows the section it sits under.

    A merged cell arrives as its text in the leftmost column and ``None`` in the
    rest, so carrying the last seen value rightwards reconstructs the span.
    Columns before the first section name have none, and get ``""``.

    A cell holding only digits is a structural marker rather than a section --
    the ART41.00 banner starts with a ``3`` above the ItemID column -- and is
    skipped, so the technical columns before the first real section stay
    unqualified instead of inheriting a number as their prefix.
    """
    out: List[str] = []
    current = ""
    for index in range(width):
        cell = section_row[index] if index < len(section_row) else None
        text = "" if cell is None else str(cell).strip()
        if text and not text.isdigit():
            current = text
        out.append(current)
    return out


def build_field_paths(
    labels: Sequence[Optional[str]],
    section_row: Optional[Sequence[Optional[str]]] = None,
    overrides: Optional[Dict[str, str]] = None,
    default_section: str = "",
) -> Dict[str, str]:
    """Map each column label to the dotted path a rule would name it by.

    ``overrides`` is keyed by the column label exactly as it appears in the
    workbook and replaces the derived path entirely, which is how a caller that
    has the template's real field metadata avoids the camel-case guess.
    """
    overrides = overrides or {}
    width = len(labels)
    sections = (
        spread_sections(section_row, width)
        if section_row is not None
        else [""] * width
    )

    paths: Dict[str, str] = {}
    for index, label in enumerate(labels):
        if label is None:
            continue
        key = str(label)
        if not key:
            continue
        if key in overrides:
            paths[key] = str(overrides[key])
            continue

        field = field_name_from_label(key)
        if not field:
            continue
        section = normalize_section(sections[index]) or normalize_section(default_section)
        paths[key] = f"{section}.{field}" if section else field
    return paths
