"""``NodeScopeProvider`` descriptor source — step 4, the ``/variable-suggestions`` half.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §6.

Building these scopes costs **N×M** file-service round-trips today: ``_enrich``
reaches ``_fetch_columns_and_preview`` once per source per path, each an HTTP
``query_file_data($top=5)``. So the table read has to be **one query for every
upstream node** — fetching per node would only trade N HTTP calls for N SQL
ones, which is not a fix.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.layer2_application.features.get_variable_suggestions.use_cases.scope_providers import (
    NodeScopeProvider,
)

_RUN = SimpleNamespace(id="run-1")


class _Logger:
    def __init__(self):
        self.warnings: list[str] = []

    def warning(self, msg, *a, **k):
        self.warnings.append(str(msg))

    def info(self, *a, **k): pass
    def error(self, *a, **k): pass
    def debug(self, *a, **k): pass


class _Repo:
    def __init__(self, values=None, boom=None):
        self.values, self.boom, self.calls = values or {}, boom, 0

    def get_latest_many(self, run_id, node_refs):
        self.calls += 1
        if self.boom:
            raise self.boom
        return {k: v for k, v in self.values.items() if k in set(node_refs)}


def _provider(mode="event", repo=None, logger=None):
    return NodeScopeProvider(
        run_repo=None, workflow_repo=None, logger=logger or _Logger(),
        node_output_repo=repo, node_output_read_mode=mode,
    )


class TestOneQueryForEveryUpstreamNode:
    def test_twenty_nodes_cost_one_call(self):
        repo = _Repo({f"n{i}": {"file_id": f"f{i}"} for i in range(20)})
        got = _provider("table", repo)._table_values(_RUN, [f"n{i}" for i in range(20)])
        assert len(got) == 20
        assert repo.calls == 1, f"{repo.calls} calls for 20 nodes — N×M all over again"

    def test_it_passes_every_upstream_id_through(self):
        seen = {}

        class _Spy(_Repo):
            def get_latest_many(self, run_id, node_refs):
                seen["run"], seen["refs"] = run_id, list(node_refs)
                return {}

        _provider("table", _Spy())._table_values(_RUN, ["a", "b", "c"])
        assert seen == {"run": "run-1", "refs": ["a", "b", "c"]}


class TestItNeverBreaksTheSuggestionsPanel:
    def test_event_mode_does_not_query_at_all(self):
        repo = _Repo({"n1": {"file_id": "x"}})
        assert _provider("event", repo)._table_values(_RUN, ["n1"]) == {}
        assert repo.calls == 0

    def test_no_repo_wired_is_empty_not_an_error(self):
        assert _provider("table", None)._table_values(_RUN, ["n1"]) == {}

    @pytest.mark.parametrize("boom", [
        RuntimeError("HANA down"), OSError("network"), ValueError("bad row"),
    ])
    def test_a_broken_read_degrades_to_the_old_source(self, boom):
        """Falling back to ``variables`` is what the endpoint has always done.
        A new table being unavailable must not empty the dropdown."""
        logger = _Logger()
        assert _provider("table", _Repo(boom=boom), logger)._table_values(
            _RUN, ["n1"]) == {}
        assert len(logger.warnings) == 1

    def test_a_repo_returning_none_is_treated_as_empty(self):
        class _NoneRepo:
            def get_latest_many(self, run_id, node_refs):
                return None

        assert _provider("table", _NoneRepo())._table_values(_RUN, ["n1"]) == {}

    @pytest.mark.parametrize("mode", ["evnet", "talbe", "", "on", "true"])
    def test_an_unknown_mode_does_not_flip_the_read(self, mode):
        """Same fail-safe as GET /output: a typo must not switch source."""
        repo = _Repo({"n1": {"file_id": "x"}})
        assert _provider(mode, repo)._table_values(_RUN, ["n1"]) == {}
        assert repo.calls == 0


class TestShadowModeQueriesButDoesNotServe:
    def test_shadow_still_reads_so_divergence_can_be_seen(self):
        repo = _Repo({"n1": {"file_id": "x"}})
        assert _provider("shadow", repo)._table_values(_RUN, ["n1"]) != {}
        assert repo.calls == 1
