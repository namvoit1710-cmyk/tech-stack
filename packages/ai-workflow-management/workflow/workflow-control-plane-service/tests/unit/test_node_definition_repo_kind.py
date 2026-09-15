"""SA-1734 Task 5: the node-definition repositories must persist and read back
both ``kind`` (deterministically re-derivable) and ``collection`` (user-set free
text that heals to "My Nodes" if lost). The memory repo stores the entity
verbatim, so this is a round-trip guard against a future refactor to a
field-allowlist store silently dropping either attribute.
"""
from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer4_frameworks.persistence.memory.memory_node_definition_repo import MemoryNodeDefinitionRepository


def test_memory_repo_round_trips_kind_collection():
    repo = MemoryNodeDefinitionRepository()
    nd = NodeDefinition(name="X", base_worker_id="http-worker", kind="read", collection="Google")
    repo.save(nd)
    got = repo.get_by_id(nd.id)
    assert got.kind == "read" and got.collection == "Google"
