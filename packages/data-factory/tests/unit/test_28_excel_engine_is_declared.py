"""The Excel engine has to be a declared dependency, not a happy accident.

polars does not depend on fastexcel or xlsxwriter. It imports them lazily and
raises only when someone actually opens or writes a workbook. So a developer
machine that has them installed for any reason runs the whole Excel test suite
green while the deployed image — built strictly from requirements.txt — cannot
read a workbook at all.

That is exactly what happened: the workbook-ingestion work shipped, every local
test passed, and the first upload of a real .xlsx to the deployed service came
back

    Transform failed: required package 'fastexcel' not found.

"Does it import here" cannot catch that, because here is the problem. So these
tests read requirements.txt.
"""

import re
from pathlib import Path

import polars as pl
import pytest

REQUIREMENTS = Path(__file__).resolve().parents[2] / "requirements.txt"

#: What each engine is for, so a failure says why it matters.
ENGINES = {
    "fastexcel": "polars.read_excel — without it the service cannot open a workbook",
    "xlsxwriter": "polars write_excel and the workbook fixtures in these tests",
}


def _declared():
    text = REQUIREMENTS.read_text(encoding="utf-8")
    names = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[=<>!\[;]", line, maxsplit=1)[0].strip().lower()
        if name:
            names.add(name)
    return names


def test_requirements_file_is_where_this_test_thinks_it_is():
    """If the layout moves, fail here rather than passing vacuously."""
    assert REQUIREMENTS.is_file(), f"no requirements.txt at {REQUIREMENTS}"
    assert "polars" in _declared()


@pytest.mark.parametrize("package,why", sorted(ENGINES.items()))
def test_the_excel_engine_is_declared_in_requirements(package, why):
    assert package in _declared(), (
        f"'{package}' is not in requirements.txt, so the deployed image will not "
        f"have it. It is needed for: {why}. It may be installed on this machine, "
        "which is why the rest of the Excel tests still pass here."
    )


@pytest.mark.parametrize("package", sorted(ENGINES))
def test_the_excel_engine_is_actually_importable(package):
    """The other half: declared is not the same as present."""
    pytest.importorskip(
        package,
        reason=f"'{package}' is declared but not installed in this environment",
    )


def test_polars_can_round_trip_a_workbook(tmp_path):
    """The behaviour the two packages exist for, exercised together."""
    path = tmp_path / "round-trip.xlsx"
    pl.DataFrame({"article": ["A100"], "plant": ["1000"]}).write_excel(path)

    back = pl.read_excel(path)

    assert back["article"].to_list() == ["A100"]
