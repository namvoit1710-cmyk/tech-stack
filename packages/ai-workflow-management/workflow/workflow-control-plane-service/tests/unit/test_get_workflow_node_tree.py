"""Unit tests for get_workflow_node_tree feature and memory repo relations."""

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from app.layer2_application.features.get_workflow_node_tree.use_cases.get_workflow_node_tree_use_case import (
    GetWorkflowNodeTreeCommand,
    GetWorkflowNodeTreeUseCase,
)

from tests.conftest import DummyLogger


def _make_workflow(wf_id, name, nodes=None, edges=None):
    return Workflow(
        id=wf_id,
        name=name,
        nodes=nodes or [],
        edges=edges or [],
    )


def _task_node(node_id, name="Task"):
    return WorkflowNode(id=node_id, name=name, node_type="TASK")


def _workflow_node(node_id, child_wf_id, name="Sub WF"):
    return WorkflowNode(
        id=node_id,
        name=name,
        node_type="WORKFLOW",
        config={"workflow_id": child_wf_id},
    )


# ── Memory repo relation tests ──────────────────────────────────


class TestMemoryRepoRelations:
    def test_save_extracts_relations(self):
        repo = MemoryWorkflowRepository()
        wf = _make_workflow("wf1", "Parent", nodes=[
            _task_node("n1"),
            _workflow_node("n2", "child-wf"),
        ])
        repo.save(wf)

        rels = repo.get_child_workflow_ids("wf1")
        assert len(rels) == 1
        assert rels[0]["child_workflow_id"] == "child-wf"
        assert rels[0]["node_id"] == "n2"

    def test_save_updates_relations_on_change(self):
        repo = MemoryWorkflowRepository()
        wf = _make_workflow("wf1", "Parent", nodes=[
            _workflow_node("n1", "child-a"),
        ])
        repo.save(wf)
        assert repo.get_child_workflow_ids("wf1")[0]["child_workflow_id"] == "child-a"

        wf.nodes = [_workflow_node("n1", "child-b")]
        repo.save(wf)
        rels = repo.get_child_workflow_ids("wf1")
        assert len(rels) == 1
        assert rels[0]["child_workflow_id"] == "child-b"

    def test_delete_clears_relations(self):
        repo = MemoryWorkflowRepository()
        wf = _make_workflow("wf1", "Parent", nodes=[_workflow_node("n1", "c1")])
        repo.save(wf)
        repo.delete("wf1")
        assert repo.get_child_workflow_ids("wf1") == []

    def test_get_workflows_by_ids(self):
        repo = MemoryWorkflowRepository()
        repo.save(_make_workflow("a", "A"))
        repo.save(_make_workflow("b", "B"))
        repo.save(_make_workflow("c", "C"))

        result = repo.get_workflows_by_ids(["a", "c", "missing"])
        assert len(result) == 2
        assert {w.id for w in result} == {"a", "c"}

    def test_get_all_child_workflows_bfs(self):
        repo = MemoryWorkflowRepository()
        repo.save(_make_workflow("root", "Root", nodes=[_workflow_node("n1", "child1")]))
        repo.save(_make_workflow("child1", "Child1", nodes=[_workflow_node("cn1", "grandchild")]))
        repo.save(_make_workflow("grandchild", "GrandChild", nodes=[_task_node("t1")]))

        result = repo.get_all_child_workflows("root")
        ids = {r["id"] for r in result}
        assert ids == {"root", "child1", "grandchild"}

    def test_no_relations_for_task_only_workflow(self):
        repo = MemoryWorkflowRepository()
        wf = _make_workflow("wf1", "NoSubs", nodes=[_task_node("t1"), _task_node("t2")])
        repo.save(wf)
        assert repo.get_child_workflow_ids("wf1") == []


# ── Use case tests ───────────────────────────────────────────────


@pytest.fixture
def repo():
    return MemoryWorkflowRepository()


@pytest.fixture
def usecase(repo):
    return GetWorkflowNodeTreeUseCase(logger=DummyLogger(), workflow_repo=repo)


class TestGetWorkflowNodeTree:
    async def test_workflow_not_found(self, usecase):
        result = await usecase.execute(GetWorkflowNodeTreeCommand(workflow_id="missing"))
        assert result.error == "Workflow not found"

    async def test_flat_workflow_no_children(self, repo, usecase):
        repo.save(_make_workflow("wf1", "Flat", nodes=[
            _task_node("t1", "Task1"),
            _task_node("t2", "Task2"),
        ]))
        result = await usecase.execute(GetWorkflowNodeTreeCommand(workflow_id="wf1"))
        assert result.error == ""
        assert result.workflow_id == "wf1"
        assert result.workflow_name == "Flat"
        assert len(result.nodes) == 2
        assert result.total_nodes == 2
        assert result.total_workflows_resolved == 0
        for node in result.nodes:
            assert node["children"] is None
            assert node["depth"] == 0
            assert node["parent_workflow_id"] == "wf1"

    async def test_single_level_nesting(self, repo, usecase):
        repo.save(_make_workflow("child", "Child WF", nodes=[
            _task_node("ct1", "ChildTask"),
        ]))
        repo.save(_make_workflow("parent", "Parent WF", nodes=[
            _task_node("t1"),
            _workflow_node("wn1", "child", "Sub WF"),
        ]))

        result = await usecase.execute(GetWorkflowNodeTreeCommand(workflow_id="parent"))
        assert result.error == ""
        assert result.total_workflows_resolved == 1

        wf_node = next(n for n in result.nodes if n["node_type"] == "WORKFLOW")
        assert wf_node["children"] is not None
        assert wf_node["children"]["workflow_id"] == "child"
        assert wf_node["children"]["workflow_name"] == "Child WF"
        assert len(wf_node["children"]["nodes"]) == 1
        assert wf_node["children"]["nodes"][0]["depth"] == 1
        assert wf_node["children"]["nodes"][0]["parent_workflow_id"] == "child"

    async def test_multi_level_nesting(self, repo, usecase):
        repo.save(_make_workflow("gc", "GrandChild", nodes=[_task_node("gt1")]))
        repo.save(_make_workflow("child", "Child", nodes=[_workflow_node("cwn", "gc")]))
        repo.save(_make_workflow("root", "Root", nodes=[_workflow_node("rwn", "child")]))

        result = await usecase.execute(GetWorkflowNodeTreeCommand(workflow_id="root"))
        assert result.total_workflows_resolved == 2

        root_wf_node = result.nodes[0]
        assert root_wf_node["children"]["workflow_id"] == "child"
        child_wf_node = root_wf_node["children"]["nodes"][0]
        assert child_wf_node["children"]["workflow_id"] == "gc"
        grandchild_task = child_wf_node["children"]["nodes"][0]
        assert grandchild_task["depth"] == 2
        assert grandchild_task["children"] is None

    async def test_circular_reference_stops(self, repo, usecase):
        repo.save(_make_workflow("a", "A", nodes=[_workflow_node("an", "b")]))
        repo.save(_make_workflow("b", "B", nodes=[_workflow_node("bn", "a")]))

        result = await usecase.execute(GetWorkflowNodeTreeCommand(workflow_id="a"))
        assert result.error == ""
        # A resolves B, but B->A is skipped (already visited)
        a_child = result.nodes[0]["children"]
        assert a_child["workflow_id"] == "b"
        b_wf_node = a_child["nodes"][0]
        # B's WORKFLOW node referencing A should have children=None (visited)
        assert b_wf_node["children"] is None

    async def test_max_depth_stops_recursion(self, repo, usecase):
        repo.save(_make_workflow("deep2", "Deep2", nodes=[_task_node("dt")]))
        repo.save(_make_workflow("deep1", "Deep1", nodes=[_workflow_node("d1n", "deep2")]))
        repo.save(_make_workflow("root", "Root", nodes=[_workflow_node("rn", "deep1")]))

        result = await usecase.execute(GetWorkflowNodeTreeCommand(
            workflow_id="root", max_depth=1,
        ))
        assert result.max_depth_reached is True
        assert result.total_workflows_resolved == 1
        # depth=1 child resolved, but its WORKFLOW child at depth=1 is capped
        child = result.nodes[0]["children"]
        assert child["workflow_id"] == "deep1"
        deep1_wf_node = child["nodes"][0]
        assert deep1_wf_node["children"] is None

    async def test_missing_child_returns_null_children(self, repo, usecase):
        repo.save(_make_workflow("parent", "Parent", nodes=[
            _workflow_node("wn", "nonexistent"),
        ]))
        result = await usecase.execute(GetWorkflowNodeTreeCommand(workflow_id="parent"))
        assert result.error == ""
        assert result.nodes[0]["children"] is None

    async def test_include_edges(self, repo, usecase):
        repo.save(_make_workflow("wf1", "WF", nodes=[
            _task_node("t1"),
            _task_node("t2"),
        ], edges=[
            WorkflowEdge(source_node_id="t1", target_node_id="t2"),
        ]))

        result = await usecase.execute(GetWorkflowNodeTreeCommand(
            workflow_id="wf1", include_edges=True,
        ))
        assert len(result.edges) == 1
        assert result.edges[0]["source_node_id"] == "t1"
        assert result.edges[0]["target_node_id"] == "t2"

    async def test_no_edges_by_default(self, repo, usecase):
        repo.save(_make_workflow("wf1", "WF", nodes=[_task_node("t1")], edges=[
            WorkflowEdge(source_node_id="t1", target_node_id="t1"),
        ]))
        result = await usecase.execute(GetWorkflowNodeTreeCommand(workflow_id="wf1"))
        assert result.edges == []

    async def test_total_nodes_counts_all_levels(self, repo, usecase):
        repo.save(_make_workflow("child", "Child", nodes=[_task_node("c1"), _task_node("c2")]))
        repo.save(_make_workflow("root", "Root", nodes=[
            _task_node("r1"),
            _workflow_node("rwn", "child"),
        ]))
        result = await usecase.execute(GetWorkflowNodeTreeCommand(workflow_id="root"))
        assert result.total_nodes == 4  # r1, rwn, c1, c2
