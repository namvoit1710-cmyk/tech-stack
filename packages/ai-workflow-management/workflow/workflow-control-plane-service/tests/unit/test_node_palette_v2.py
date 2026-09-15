"""Tests for metadata in the node palette response."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from app.layer2_application.features.list_node_palette.use_cases.list_node_palette_use_case import (
    ListNodePaletteCommand,
    ListNodePaletteUseCase,
    BUILTIN_NODES,
)


class DummyLogger:
    def info(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def debug(self, *a, **kw): pass
    def warning(self, *a, **kw): pass


class TestBuiltinNodesMetadata:
    def test_all_builtin_nodes_have_icon(self):
        for node in BUILTIN_NODES:
            assert "icon" in node, f"Node {node['id']} missing icon"
            assert node["icon"], f"Node {node['id']} has empty icon"

    def test_all_builtin_nodes_have_color(self):
        for node in BUILTIN_NODES:
            assert "color" in node, f"Node {node['id']} missing color"
            assert node["color"].startswith("#"), f"Node {node['id']} has invalid color"

    def test_all_builtin_nodes_have_node_class(self):
        for node in BUILTIN_NODES:
            assert "node_class" in node
            assert node["node_class"] in ("TECHNICAL", "BUSINESS")

    def test_all_builtin_nodes_have_ports(self):
        for node in BUILTIN_NODES:
            assert "ports" in node
            ports = node["ports"]
            assert "in" in ports or "out" in ports

    def test_condition_has_two_out_ports(self):
        condition = next(n for n in BUILTIN_NODES if n["node_type"] == "CONDITION")
        out_ids = {p["id"] for p in condition["ports"]["out"]}
        assert "true_branch" in out_ids
        assert "false_branch" in out_ids

    def test_trigger_has_no_in_ports(self):
        trigger = next(n for n in BUILTIN_NODES if n["node_type"] == "TRIGGER")
        assert len(trigger["ports"]["in"]) == 0


class TestListNodePaletteUseCase:
    @pytest.mark.asyncio
    async def test_palette_includes_node_types(self):
        uc = ListNodePaletteUseCase(logger=DummyLogger())
        result = await uc.execute(ListNodePaletteCommand())
        all_nodes = [n for cat in result.categories.values() for n in cat]
        assert len(all_nodes) >= len(BUILTIN_NODES)
        for entry in all_nodes:
            assert "node_type" in entry
            assert "icon" in entry
            assert "color" in entry
            assert "node_class" in entry
            assert "ports" in entry

    @pytest.mark.asyncio
    async def test_palette_categories_present(self):
        uc = ListNodePaletteUseCase(logger=DummyLogger())
        result = await uc.execute(ListNodePaletteCommand())
        assert "control_flow" in result.categories
        assert "worker" in result.categories
        assert result.total >= len(BUILTIN_NODES)


class _FakeWorkerRegistryClient:
    """Returns a canned worker list for palette tests."""
    def __init__(self, workers: list[dict]):
        self._workers = workers

    def list_workers(self) -> list[dict]:
        return list(self._workers)


class TestWorkerTypeStability:
    @pytest.mark.asyncio
    async def test_palette_skips_worker_with_empty_type(self):
        """A worker registered with empty worker_type must NOT be surfaced
        as a palette entry with its UUID. It should be dropped entirely."""
        client = _FakeWorkerRegistryClient([
            {
                "id": "uuid-broken",
                "worker_type": "",
                "name": "misconfigured",
                "health_status": "healthy",
            },
            {
                "id": "uuid-ok",
                "worker_type": "http-worker",
                "name": "HTTP",
                "health_status": "healthy",
            },
        ])
        uc = ListNodePaletteUseCase(logger=DummyLogger(), worker_registry_client=client)
        result = await uc.execute(ListNodePaletteCommand())
        worker_nodes = result.categories["worker"]
        # Only the valid worker shows up.
        assert len(worker_nodes) == 1
        assert worker_nodes[0]["worker_id"] == "http-worker"
        # No node has the UUID as its id.
        for node in worker_nodes:
            assert node["id"] != "worker:uuid-broken"
            assert "uuid-broken" not in node["id"]

    @pytest.mark.asyncio
    async def test_palette_keeps_worker_with_valid_type(self):
        client = _FakeWorkerRegistryClient([
            {
                "id": "uuid-1",
                "worker_type": "agent-worker",
                "name": "Agent",
                "health_status": "healthy",
            },
        ])
        uc = ListNodePaletteUseCase(logger=DummyLogger(), worker_registry_client=client)
        result = await uc.execute(ListNodePaletteCommand())
        worker_nodes = result.categories["worker"]
        assert len(worker_nodes) == 1
        assert worker_nodes[0]["id"] == "worker:agent-worker"


class TestSpecVersion:
    """Every palette entry must carry a spec_version string."""

    _SEMVER_RE = r"^\d+\.\d+\.\d+"

    def test_all_builtin_nodes_have_spec_version(self):
        import re
        for node in BUILTIN_NODES:
            assert "spec_version" in node, f"{node['id']} missing spec_version"
            assert re.match(self._SEMVER_RE, node["spec_version"]), \
                f"{node['id']} spec_version={node['spec_version']} not semver"

    @pytest.mark.asyncio
    async def test_every_palette_node_has_spec_version(self):
        import re
        client = _FakeWorkerRegistryClient([
            {
                "id": "uuid-1",
                "worker_type": "http-worker",
                "name": "HTTP",
                "spec_version": "2.1.0",
                "health_status": "healthy",
            },
        ])
        uc = ListNodePaletteUseCase(logger=DummyLogger(), worker_registry_client=client)
        result = await uc.execute(ListNodePaletteCommand())
        all_nodes = [n for cat in result.categories.values() for n in cat]
        for node in all_nodes:
            assert "spec_version" in node, f"{node.get('id')} missing spec_version"
            assert re.match(self._SEMVER_RE, node["spec_version"])

    @pytest.mark.asyncio
    async def test_worker_spec_version_passthrough(self):
        client = _FakeWorkerRegistryClient([
            {
                "id": "uuid-1",
                "worker_type": "http-worker",
                "name": "HTTP",
                "spec_version": "2.1.0",
                "health_status": "healthy",
            },
        ])
        uc = ListNodePaletteUseCase(logger=DummyLogger(), worker_registry_client=client)
        result = await uc.execute(ListNodePaletteCommand())
        worker = result.categories["worker"][0]
        assert worker["spec_version"] == "2.1.0"

    @pytest.mark.asyncio
    async def test_worker_spec_version_defaults_when_missing(self):
        client = _FakeWorkerRegistryClient([
            {
                "id": "uuid-1",
                "worker_type": "legacy-worker",
                "name": "Legacy",
                "health_status": "healthy",
                # no spec_version field at all
            },
        ])
        uc = ListNodePaletteUseCase(logger=DummyLogger(), worker_registry_client=client)
        result = await uc.execute(ListNodePaletteCommand())
        worker = result.categories["worker"][0]
        assert worker["spec_version"] == "1.0.0"


def _hb(seconds_ago: int) -> str:
    """An ISO-8601 last_heartbeat `seconds_ago` in the past (UTC)."""
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


class TestWorkerFreshnessAndDedupe:
    """SA-2052 (R5) on the legacy v1 path: dedupe by worker_type + freshness."""

    @pytest.mark.asyncio
    async def test_replicas_of_one_type_collapse_to_one_node(self):
        client = _FakeWorkerRegistryClient([
            {"id": "uuid-1", "worker_type": "t", "name": "T", "health_status": "healthy", "last_heartbeat": _hb(5)},
            {"id": "uuid-2", "worker_type": "t", "name": "T", "health_status": "healthy", "last_heartbeat": _hb(10)},
            {"id": "uuid-3", "worker_type": "t", "name": "T", "health_status": "healthy", "last_heartbeat": _hb(15)},
        ])
        uc = ListNodePaletteUseCase(logger=DummyLogger(), worker_registry_client=client)
        result = await uc.execute(ListNodePaletteCommand())
        worker_nodes = result.categories["worker"]
        assert len(worker_nodes) == 1
        assert worker_nodes[0]["id"] == "worker:t"

    @pytest.mark.asyncio
    async def test_stale_healthy_worker_is_excluded(self):
        client = _FakeWorkerRegistryClient([
            {"id": "uuid-1", "worker_type": "t", "name": "T", "health_status": "healthy", "last_heartbeat": _hb(200)},
        ])
        uc = ListNodePaletteUseCase(logger=DummyLogger(), worker_registry_client=client)
        result = await uc.execute(ListNodePaletteCommand())
        assert result.categories["worker"] == []

    @pytest.mark.asyncio
    async def test_fresh_healthy_worker_is_shown(self):
        client = _FakeWorkerRegistryClient([
            {"id": "uuid-1", "worker_type": "t", "name": "T", "health_status": "healthy", "last_heartbeat": _hb(10)},
        ])
        uc = ListNodePaletteUseCase(logger=DummyLogger(), worker_registry_client=client)
        result = await uc.execute(ListNodePaletteCommand())
        ids = {n["id"] for n in result.categories["worker"]}
        assert ids == {"worker:t"}

    @pytest.mark.asyncio
    async def test_one_stale_one_fresh_shown_once(self):
        client = _FakeWorkerRegistryClient([
            {"id": "stale", "worker_type": "t", "name": "T", "health_status": "healthy", "last_heartbeat": _hb(200)},
            {"id": "fresh", "worker_type": "t", "name": "T", "health_status": "healthy", "last_heartbeat": _hb(5)},
        ])
        uc = ListNodePaletteUseCase(logger=DummyLogger(), worker_registry_client=client)
        result = await uc.execute(ListNodePaletteCommand())
        assert len(result.categories["worker"]) == 1

    @pytest.mark.asyncio
    async def test_grpc_shaped_row_without_heartbeat_is_kept(self):
        client = _FakeWorkerRegistryClient([
            {"id": "uuid-1", "worker_type": "t", "name": "T", "health_status": "healthy"},  # no last_heartbeat
        ])
        uc = ListNodePaletteUseCase(logger=DummyLogger(), worker_registry_client=client)
        result = await uc.execute(ListNodePaletteCommand())
        ids = {n["id"] for n in result.categories["worker"]}
        assert ids == {"worker:t"}

    @pytest.mark.asyncio
    async def test_http_blank_heartbeat_is_excluded(self):
        client = _FakeWorkerRegistryClient([
            {"id": "uuid-1", "worker_type": "t", "name": "T", "health_status": "healthy", "last_heartbeat": ""},
        ])
        uc = ListNodePaletteUseCase(logger=DummyLogger(), worker_registry_client=client)
        result = await uc.execute(ListNodePaletteCommand())
        assert result.categories["worker"] == []

    @pytest.mark.asyncio
    async def test_reads_stale_window_from_app_config(self):
        """A worker 90s silent is EXCLUDED when app_config sets a 60s window."""
        class _Config:
            WORKER_STALE_AFTER_SECONDS = 60

        client = _FakeWorkerRegistryClient([
            {"id": "uuid-1", "worker_type": "t", "name": "T", "health_status": "healthy", "last_heartbeat": _hb(90)},
        ])
        uc = ListNodePaletteUseCase(
            logger=DummyLogger(), worker_registry_client=client, app_config=_Config(),
        )
        result = await uc.execute(ListNodePaletteCommand())
        assert result.categories["worker"] == []

    @pytest.mark.asyncio
    async def test_stale_window_zero_is_honored_not_defaulted(self):
        """WORKER_STALE_AFTER_SECONDS=0 must be honored (window 0 -> nothing fresh),
        NOT swallowed back to 120. Parity with v2, which takes the int directly.
        Teeth: the prior `getattr(...) or 120` turned 0 into 120, so this fresh
        worker would have been KEPT."""
        class _Config:
            WORKER_STALE_AFTER_SECONDS = 0

        client = _FakeWorkerRegistryClient([
            {"id": "uuid-1", "worker_type": "t", "name": "T", "health_status": "healthy", "last_heartbeat": _hb(5)},
        ])
        uc = ListNodePaletteUseCase(
            logger=DummyLogger(), worker_registry_client=client, app_config=_Config(),
        )
        result = await uc.execute(ListNodePaletteCommand())
        assert result.categories["worker"] == []
