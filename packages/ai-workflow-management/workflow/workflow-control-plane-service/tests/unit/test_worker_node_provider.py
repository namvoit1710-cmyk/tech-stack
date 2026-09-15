"""Tests for WorkerNodeProvider — projects executor workers into the catalog."""

from datetime import datetime, timezone, timedelta

import pytest

from app.layer2_application.providers import WorkerNodeProvider
from app.layer2_application.contracts import (
    NodeDefinitionDetail,
    NodeDefinitionSummary,
    NodeRuntime,
)


class _FakeClient:
    def __init__(self, workers: list[dict]) -> None:
        self._workers = workers

    def list_workers(self) -> list[dict]:
        return list(self._workers)

    def get_worker_by_type(self, worker_type: str) -> dict | None:
        for w in self._workers:
            if w.get("worker_type") == worker_type:
                return w
        return None


def _valid_form_field(key: str = "url") -> dict:
    return {
        "key": key,
        "outputType": "string",
        "fieldConfig": {"fieldControl": "InputControl"},
    }


def _sample_worker(**overrides) -> dict:
    base = {
        "id": "uuid-1",
        "worker_type": "http-request",
        "name": "HTTP",
        "description": "Makes HTTP calls",
        "version": "1.2.3",
        "spec_version": "2.0.0",
        "health_status": "healthy",
        "icon": "Globe",
        "color": "#4A90E2",
        "tags": ["io", "http"],
        "capabilities": [{"domain": "http", "action": "call"}],
        "ports": {
            "in": [{"id": "in_default", "label": "Input"}],
            "out": [{"id": "out_default", "label": "Output"}],
        },
        "input_schema": [_valid_form_field("url")],
        "output_schema": [_valid_form_field("body")],
    }
    base.update(overrides)
    return base


class TestListSummaries:
    def test_maps_worker_to_summary(self):
        client = _FakeClient([_sample_worker()])
        p = WorkerNodeProvider(client)
        summaries = p.list_summaries()
        assert len(summaries) == 1
        s = summaries[0]
        assert isinstance(s, NodeDefinitionSummary)
        assert s.id == "worker:http-request"
        assert s.provider == "worker"
        assert s.node_type == "TASK"
        assert s.runtime == NodeRuntime.WORKER
        assert s.spec_version == "2.0.0"

    def test_skips_empty_worker_type(self):
        client = _FakeClient([
            _sample_worker(worker_type="", id="bad-uuid"),
            _sample_worker(),
        ])
        p = WorkerNodeProvider(client)
        summaries = p.list_summaries()
        assert len(summaries) == 1
        assert summaries[0].id == "worker:http-request"

    def test_skips_unhealthy_workers(self):
        client = _FakeClient([
            _sample_worker(worker_type="a", health_status="unhealthy"),
            _sample_worker(worker_type="b", health_status="degraded"),
            _sample_worker(worker_type="c", health_status="healthy"),
        ])
        p = WorkerNodeProvider(client)
        ids = {s.id for s in p.list_summaries()}
        assert ids == {"worker:c"}

    def test_empty_health_status_accepted(self):
        """Defensive: some registrations omit health_status entirely."""
        client = _FakeClient([_sample_worker(health_status=None)])
        p = WorkerNodeProvider(client)
        assert len(p.list_summaries()) == 1

    def test_spec_version_defaults_when_missing(self):
        client = _FakeClient([_sample_worker(spec_version=None)])
        p = WorkerNodeProvider(client)
        assert p.list_summaries()[0].spec_version == "1.0.0"

    def test_skips_worker_with_no_io_port(self):
        """Domain rule: a worker must declare at least one input or output port."""
        client = _FakeClient([
            _sample_worker(worker_type="noports", ports={}),
            _sample_worker(worker_type="missing", ports=None),
            _sample_worker(worker_type="emptylists", ports={"in": [], "out": []}),
            _sample_worker(worker_type="ok"),
        ])
        p = WorkerNodeProvider(client)
        ids = {s.id for s in p.list_summaries()}
        assert ids == {"worker:ok"}

    def test_keeps_worker_with_only_output_port(self):
        """One side is enough — input *or* output."""
        client = _FakeClient([
            _sample_worker(worker_type="out-only", ports={"out": [{"id": "out_default"}]}),
        ])
        p = WorkerNodeProvider(client)
        assert {s.id for s in p.list_summaries()} == {"worker:out-only"}


class TestGetDetail:
    def test_returns_detail_for_known_worker(self):
        client = _FakeClient([_sample_worker()])
        p = WorkerNodeProvider(client)
        d = p.get_detail("worker:http-request")
        assert isinstance(d, NodeDefinitionDetail)
        assert d.worker_type == "http-request"
        assert len(d.input_schema) == 1
        assert d.input_schema[0].key == "url"

    def test_returns_none_for_unknown(self):
        p = WorkerNodeProvider(_FakeClient([]))
        assert p.get_detail("worker:missing") is None

    def test_returns_none_for_wrong_prefix(self):
        p = WorkerNodeProvider(_FakeClient([_sample_worker()]))
        assert p.get_detail("builtin:anything") is None
        assert p.get_detail("business:anything") is None

    def test_empty_worker_type_in_ref_returns_none(self):
        p = WorkerNodeProvider(_FakeClient([_sample_worker()]))
        assert p.get_detail("worker:") is None

    def test_returns_none_for_worker_without_io_port(self):
        p = WorkerNodeProvider(_FakeClient([_sample_worker(ports={})]))
        assert p.get_detail("worker:http-request") is None

    def test_spec_version_match_succeeds(self):
        p = WorkerNodeProvider(_FakeClient([_sample_worker(spec_version="3.1.0")]))
        d = p.get_detail("worker:http-request", spec_version="3.1.0")
        assert d is not None

    def test_spec_version_mismatch_raises(self):
        p = WorkerNodeProvider(_FakeClient([_sample_worker(spec_version="2.0.0")]))
        with pytest.raises(ValueError, match="spec_version"):
            p.get_detail("worker:http-request", spec_version="1.0.0")


class TestCredentialAffinitySeeding:
    """The catalog seeds supported_credential_kinds for verified workers (FE picker)."""

    def test_email_worker_detail_carries_vault_kinds(self):
        p = WorkerNodeProvider(_FakeClient([_sample_worker(worker_type="email")]))
        d = p.get_detail("worker:email")
        assert d is not None
        assert d.supported_credential_kinds == ["BASIC_AUTH", "OAUTH2_APPLICATION"]

    def test_ms_teams_worker_detail_carries_webhook_kind(self):
        """SA-1558: ms_teams picker filters to WEBHOOK_URL credentials (canonical
        credentials-management kind; the earlier lowercase 'webhook_url' never matched
        the picker's case-sensitive filter)."""
        p = WorkerNodeProvider(_FakeClient([_sample_worker(worker_type="ms_teams")]))
        d = p.get_detail("worker:ms_teams")
        assert d is not None
        assert d.supported_credential_kinds == ["WEBHOOK_URL"]

    def test_ms_teams_meeting_detail_carries_oauth_kind(self):
        """SA-1549: new ms_teams_meeting node seeds Graph client-credentials kind."""
        p = WorkerNodeProvider(_FakeClient([_sample_worker(worker_type="ms_teams_meeting")]))
        d = p.get_detail("worker:ms_teams_meeting")
        assert d is not None
        assert d.supported_credential_kinds == ["OAUTH2_APPLICATION"]

    def test_ms_teams_channel_detail_carries_oauth_kind(self):
        """SA-1550: ms_teams_channel node seeds Graph client-credentials kind."""
        p = WorkerNodeProvider(_FakeClient([_sample_worker(worker_type="ms_teams_channel")]))
        d = p.get_detail("worker:ms_teams_channel")
        assert d is not None
        assert d.supported_credential_kinds == ["OAUTH2_APPLICATION"]

    def test_agent_detail_carries_api_key_kind(self):
        """SA-1563: agent picker filters to API_KEY (was unfiltered via FE fallback)."""
        p = WorkerNodeProvider(_FakeClient([_sample_worker(worker_type="agent")]))
        d = p.get_detail("worker:agent")
        assert d is not None and d.supported_credential_kinds == ["API_KEY"]

    def test_database_connection_detail_carries_db_kinds(self):
        """SA-1563: database_connection picker filters to DATABASE_CONNECTION/BASIC_AUTH."""
        p = WorkerNodeProvider(_FakeClient([_sample_worker(worker_type="database-connection")]))
        d = p.get_detail("worker:database-connection")
        assert d is not None and d.supported_credential_kinds == ["DATABASE_CONNECTION", "BASIC_AUTH"]

    def test_http_request_detail_carries_broad_kinds(self):
        """SA-1563: http_request picker filters to a curated broad set (not show-all)."""
        p = WorkerNodeProvider(_FakeClient([_sample_worker(worker_type="http_request")]))
        d = p.get_detail("worker:http_request")
        assert d is not None
        assert d.supported_credential_kinds == ["API_KEY", "BASIC_AUTH", "PERSONAL_ACCESS_TOKEN", "JWT", "OAUTH2_APPLICATION"]

    def test_jira_worker_nodes_carry_api_key_via_prefix(self):
        """jira-worker registers 22 ``jira_*`` node types sharing the vault
        jira-api-token (kind API_KEY). A family PREFIX seeds them all, so each node's
        picker filters to Jira credentials without 22 exact entries."""
        for wt in ("jira_create_issue", "jira_get_issue", "jira_search", "jira_transition_issue"):
            p = WorkerNodeProvider(_FakeClient([_sample_worker(worker_type=wt)]))
            d = p.get_detail(f"worker:{wt}")
            assert d is not None
            assert d.supported_credential_kinds == ["API_KEY"], wt

    def test_exact_affinity_wins_over_prefix(self):
        """An exact _CREDENTIAL_AFFINITY entry overrides any family prefix; unknown
        worker_types fall through to empty (FE fallback)."""
        from app.layer2_application.providers.worker_node_provider import _credential_kinds_for
        assert _credential_kinds_for("ms_teams") == ["WEBHOOK_URL"]
        assert _credential_kinds_for("jira_create_issue") == ["API_KEY"]
        assert _credential_kinds_for("totally_unknown") == []

    def test_only_the_two_jira_facing_req2tpl_nodes_carry_a_credential_kind(self):
        """req2tpl-worker registers nine ``req2tpl_*`` nodes, but only the two that
        talk to Jira consume a credential — the rest drive a local CLI. They are
        seeded EXACTLY rather than by prefix so the other seven do not render a
        credential picker with nothing to authenticate."""
        from app.layer2_application.providers.worker_node_provider import _credential_kinds_for
        assert _credential_kinds_for("req2tpl_fetch_inputs") == ["API_KEY"]
        assert _credential_kinds_for("req2tpl_attach_result") == ["API_KEY"]
        for wt in ("req2tpl_build", "req2tpl_map", "req2tpl_verify", "req2tpl_init",
                   "req2tpl_survey", "req2tpl_write_profile", "req2tpl_upload_payload"):
            assert _credential_kinds_for(wt) == [], wt

    def test_req2tpl_nodes_group_under_one_palette_collection(self):
        """The prefix earns its keep for browsing even though affinity is per node."""
        from app.layer2_application.providers.worker_node_provider import _worker_collection
        assert _worker_collection("req2tpl_build") == "req2tpl"
        assert _worker_collection("req2tpl_fetch_inputs") == "req2tpl"
        assert _worker_collection("jira_create_issue") == "Jira"

    def test_every_seeded_prefix_kind_is_valid(self):
        """Drift-guard (SA-1558 shape) extended to the prefix families."""
        from app.layer1_domain.entities.node_definition import NodeDefinition
        from app.layer2_application.providers.worker_node_provider import _CREDENTIAL_AFFINITY_PREFIXES
        valid = set(NodeDefinition.VALID_CREDENTIAL_KINDS)
        for prefix, kinds in _CREDENTIAL_AFFINITY_PREFIXES.items():
            for kind in kinds:
                assert kind in valid, f"{prefix} seeds unknown kind {kind!r}"

    def test_every_seeded_kind_is_a_valid_credential_kind(self):
        """SA-1558 drift-guard: every kind seeded into the picker must be in the
        NodeDefinition vocab (which SA-1558 aligned to the credentials-management
        canonical kinds). Catches future lowercase/uppercase drift that would silently
        empty the picker."""
        from app.layer1_domain.entities.node_definition import NodeDefinition
        from app.layer2_application.providers.worker_node_provider import _CREDENTIAL_AFFINITY
        valid = set(NodeDefinition.VALID_CREDENTIAL_KINDS)
        for worker_type, kinds in _CREDENTIAL_AFFINITY.items():
            for kind in kinds:
                assert kind in valid, f"{worker_type} seeds unknown kind {kind!r}"
                assert kind == kind.upper() or kind in valid, kind

    def test_unlisted_worker_detail_has_no_kinds(self):
        """Workers without a verified affinity keep the FE fallback (empty kinds)."""
        p = WorkerNodeProvider(_FakeClient([_sample_worker()]))
        d = p.get_detail("worker:http-request")
        assert d is not None
        assert d.supported_credential_kinds == []


class TestMalformedSchemaHandling:
    def test_lenient_mode_drops_bad_field_and_keeps_others(self):
        """Default (non-strict) keeps the good fields and drops the bad."""
        bad = {"key": "x", "outputType": "nonsense_type", "fieldConfig": {}}
        good = _valid_form_field("ok")
        w = _sample_worker(input_schema=[bad, good])
        p = WorkerNodeProvider(_FakeClient([w]))
        d = p.get_detail("worker:http-request")
        assert d is not None
        assert len(d.input_schema) == 1
        assert d.input_schema[0].key == "ok"

    def test_strict_mode_raises_on_bad_field(self):
        bad = {"key": "x", "outputType": "nonsense_type", "fieldConfig": {}}
        w = _sample_worker(input_schema=[bad])
        p = WorkerNodeProvider(_FakeClient([w]), strict_schema_validation=True)
        with pytest.raises(Exception):
            p.get_detail("worker:http-request")


def _hb(seconds_ago: int) -> str:
    """An ISO-8601 last_heartbeat `seconds_ago` in the past (UTC)."""
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


class TestFreshnessAndDedupe:
    """SA-2052 (R5): one summary per worker_type, iff >=1 fresh healthy replica."""

    def test_replicas_of_one_type_collapse_to_one_summary(self):
        """3 healthy fresh replicas (distinct source id, same worker_type) -> ONE node."""
        client = _FakeClient([
            _sample_worker(worker_type="t", id="uuid-1", last_heartbeat=_hb(5)),
            _sample_worker(worker_type="t", id="uuid-2", last_heartbeat=_hb(10)),
            _sample_worker(worker_type="t", id="uuid-3", last_heartbeat=_hb(15)),
        ])
        p = WorkerNodeProvider(client)
        summaries = p.list_summaries()
        assert len(summaries) == 1
        assert summaries[0].id == "worker:t"

    def test_stale_healthy_instance_is_excluded(self):
        """Silent longer than the window but still health_status=healthy -> excluded."""
        client = _FakeClient([_sample_worker(worker_type="t", last_heartbeat=_hb(200))])
        p = WorkerNodeProvider(client)
        assert p.list_summaries() == []

    def test_fresh_healthy_instance_is_shown(self):
        client = _FakeClient([_sample_worker(worker_type="t", last_heartbeat=_hb(10))])
        p = WorkerNodeProvider(client)
        assert {s.id for s in p.list_summaries()} == {"worker:t"}

    def test_one_stale_one_fresh_of_same_type_shown_once(self):
        client = _FakeClient([
            _sample_worker(worker_type="t", id="stale", last_heartbeat=_hb(200)),
            _sample_worker(worker_type="t", id="fresh", last_heartbeat=_hb(5)),
        ])
        p = WorkerNodeProvider(client)
        summaries = p.list_summaries()
        assert len(summaries) == 1
        assert summaries[0].id == "worker:t"

    def test_all_stale_of_a_type_not_shown(self):
        client = _FakeClient([
            _sample_worker(worker_type="t", id="s0", last_heartbeat=_hb(200)),
            _sample_worker(worker_type="t", id="s1", last_heartbeat=_hb(300)),
        ])
        p = WorkerNodeProvider(client)
        assert p.list_summaries() == []

    def test_grpc_shaped_row_without_heartbeat_is_kept_when_healthy(self):
        """No last_heartbeat key (gRPC can't report it) + healthy -> shown (degrade)."""
        w = _sample_worker(worker_type="t")
        w.pop("last_heartbeat", None)  # base has none; be explicit about the gRPC shape
        client = _FakeClient([w])
        p = WorkerNodeProvider(client)
        assert {s.id for s in p.list_summaries()} == {"worker:t"}

    def test_grpc_shaped_row_without_heartbeat_still_excluded_when_unhealthy(self):
        w = _sample_worker(worker_type="t", health_status="unhealthy")
        w.pop("last_heartbeat", None)
        client = _FakeClient([w])
        p = WorkerNodeProvider(client)
        assert p.list_summaries() == []

    def test_http_blank_heartbeat_is_excluded(self):
        """Present-but-blank last_heartbeat="" -> measurable -> maximally stale -> excluded."""
        client = _FakeClient([_sample_worker(worker_type="t", last_heartbeat="")])
        p = WorkerNodeProvider(client)
        assert p.list_summaries() == []

    def test_hana_space_format_fresh_ts_is_not_excluded(self):
        """SA-2058 guard: a fresh HANA-format ts must NOT be filtered (parse_ts, not raw compare)."""
        hb = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")
        client = _FakeClient([_sample_worker(worker_type="t", last_heartbeat=hb)])
        p = WorkerNodeProvider(client)
        assert {s.id for s in p.list_summaries()} == {"worker:t"}

    def test_rolling_deploy_represented_by_freshest_healthy_spec(self):
        """Two live versions of a type -> one summary carrying the freshest healthy spec."""
        client = _FakeClient([
            _sample_worker(worker_type="t", id="v1", spec_version="1.0.0", last_heartbeat=_hb(60)),
            _sample_worker(worker_type="t", id="v2", spec_version="2.0.0", last_heartbeat=_hb(5)),
        ])
        p = WorkerNodeProvider(client)
        summaries = p.list_summaries()
        assert len(summaries) == 1
        assert summaries[0].spec_version == "2.0.0"
