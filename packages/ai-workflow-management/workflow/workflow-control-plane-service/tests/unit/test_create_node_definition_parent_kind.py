import asyncio

from app.layer2_application.features.create_node_definition.use_cases.create_node_definition_use_case import (
    CreateNodeDefinitionUseCase,
    CreateNodeDefinitionCommand,
)
from app.layer4_frameworks.persistence.memory.memory_node_definition_repo import (
    MemoryNodeDefinitionRepository,
)


class _Log:
    def info(self, *a, **k):
        pass
    warning = error = debug = info


def _mk(repo):
    return CreateNodeDefinitionUseCase(logger=_Log(), node_definition_repo=repo)


def test_child_inherits_parent_kind_when_not_specified():
    repo = MemoryNodeDefinitionRepository()
    uc = _mk(repo)
    parent = asyncio.run(uc.execute(CreateNodeDefinitionCommand(
        tenant_id="t1", name="Find Issues", base_worker_id="jira",
        locked_defaults={"method": "GET"},   # -> read
    )))
    assert parent.kind == "read"

    child = asyncio.run(uc.execute(CreateNodeDefinitionCommand(
        tenant_id="t1", name="Find My Issues", parent_definition_id=parent.id,
        # no kind, no GET/POST verb of its own -> must inherit parent's "read",
        # NOT the "action" fallback the pure default would give.
    )))
    assert child.kind == "read"


def test_explicit_child_kind_overrides_inheritance():
    repo = MemoryNodeDefinitionRepository()
    uc = _mk(repo)
    parent = asyncio.run(uc.execute(CreateNodeDefinitionCommand(
        tenant_id="t1", name="P", base_worker_id="jira", locked_defaults={"method": "GET"},
    )))
    child = asyncio.run(uc.execute(CreateNodeDefinitionCommand(
        tenant_id="t1", name="C", parent_definition_id=parent.id, kind="util",
    )))
    assert child.kind == "util"
