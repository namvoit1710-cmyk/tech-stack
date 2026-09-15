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


def test_create_persists_kind_collection():
    uc = CreateNodeDefinitionUseCase(
        logger=_Log(), node_definition_repo=MemoryNodeDefinitionRepository()
    )
    res = asyncio.run(
        uc.execute(
            CreateNodeDefinitionCommand(
                name="Google Search",
                base_worker_id="http-worker",
                locked_defaults={"method": "GET"},
                collection="Google",
            )
        )
    )
    assert res.kind == "read"  # healed from GET verb by the entity
    assert res.collection == "Google"
