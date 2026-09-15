"""Where ``GET /nodes/{id}/output`` reads a descriptor from — step 4.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §6.

Staged the way this service already migrated the projector (live / shadow /
projector), for the same reason: flipping a read path blind is how a
silently-wrong value reaches the UI. The default is ``event`` — the table held
**zero rows** when this was written, and a mode that returns nothing for every
historical run is worse than one that changes nothing.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.layer2_application.features.get_node_output.use_cases.get_node_output_use_case import (
    GetNodeOutputUseCase,
)

_EVENT = {"__file_ref": True, "file_id": "from-event"}
_TABLE = {"__file_ref": True, "file_id": "from-table"}
_NODE = SimpleNamespace(id="n1")


class _Logger:
    def __init__(self):
        self.warnings: list[str] = []

    def warning(self, msg, *a, **k):
        self.warnings.append(str(msg) % a if a else str(msg))

    def info(self, *a, **k): pass
    def error(self, *a, **k): pass
    def debug(self, *a, **k): pass


class _Repo:
    def __init__(self, value=_TABLE, boom=None):
        self.value, self.boom, self.calls = value, boom, 0

    def get_latest(self, run_id, node_ref):
        self.calls += 1
        if self.boom:
            raise self.boom
        return self.value


def _uc(mode="event", repo=None, logger=None):
    uc = GetNodeOutputUseCase.__new__(GetNodeOutputUseCase)
    uc._node_output_read_mode = mode
    uc._node_output_repo = repo
    uc.logger = logger or _Logger()
    return uc


def _resolve(uc, from_event=_EVENT, run_id="run-1"):
    return uc._resolve_descriptor(run_id=run_id, node=_NODE, from_event=from_event)


class TestDefaultChangesNothing:
    def test_event_mode_ignores_the_table_entirely(self):
        repo = _Repo()
        assert _resolve(_uc("event", repo)) == _EVENT
        assert repo.calls == 0, "event mode must not even query"

    def test_no_repo_wired_is_the_old_behaviour(self):
        assert _resolve(_uc("table", None)) == _EVENT

    def test_a_missing_run_id_falls_back(self):
        """``state_updates`` is keyed off a payload that may not carry run_id;
        without one there is nothing to query by."""
        repo = _Repo()
        assert _resolve(_uc("table", repo), run_id="") == _EVENT
        assert repo.calls == 0


class TestShadowReportsWithoutServing:
    def test_it_serves_the_event_value_even_when_they_differ(self):
        logger = _Logger()
        assert _resolve(_uc("shadow", _Repo(_TABLE), logger)) == _EVENT

    def test_a_divergence_is_reported(self):
        logger = _Logger()
        _resolve(_uc("shadow", _Repo(_TABLE), logger))
        assert len(logger.warnings) == 1
        assert "divergence" in logger.warnings[0]

    def test_agreement_is_silent(self):
        logger = _Logger()
        _resolve(_uc("shadow", _Repo(_EVENT), logger))
        assert logger.warnings == []

    def test_a_row_the_table_does_not_have_yet_is_not_a_divergence(self):
        """Every run older than the write path has no row. Reporting those
        would bury a real divergence in noise."""
        logger = _Logger()
        _resolve(_uc("shadow", _Repo(None), logger))
        assert logger.warnings == []


class TestTableModeStillCannotBlankTheUi:
    def test_it_serves_the_table_when_the_row_exists(self):
        assert _resolve(_uc("table", _Repo(_TABLE))) == _TABLE

    def test_a_historical_run_falls_back_to_the_event(self):
        """The table started empty. Returning None here would blank the Output
        panel for every run that completed before step 2b shipped."""
        assert _resolve(_uc("table", _Repo(None))) == _EVENT

    @pytest.mark.parametrize("boom", [
        RuntimeError("HANA down"), ValueError("bad row"), OSError("network"),
    ])
    def test_a_broken_repo_degrades_to_the_event_and_says_so(self, boom):
        logger = _Logger()
        assert _resolve(_uc("table", _Repo(boom=boom), logger)) == _EVENT
        assert len(logger.warnings) == 1
        assert "run-1" in logger.warnings[0]


class TestAnUnknownModeFailsSafe:
    """The first version of this test was written with ``or True`` and could
    not fail. Removing that exposed a real defect: an unrecognised mode fell
    through to the table branch, so ``NODE_OUTPUT_READ_MODE=talbe`` would have
    quietly served from a table that may not hold the row. Unknown now means
    "event"."""

    @pytest.mark.parametrize("mode", ["evnet", "talbe", "", "TABLE ", "on", "true"])
    def test_a_typo_does_not_flip_the_read_path(self, mode):
        repo = _Repo()
        assert _resolve(_uc(mode, repo)) == _EVENT
        assert repo.calls == 0, f"mode {mode!r} queried the table"

    def test_the_two_real_modes_still_work(self):
        assert _resolve(_uc("shadow", _Repo(_TABLE))) == _EVENT
        assert _resolve(_uc("table", _Repo(_TABLE))) == _TABLE
