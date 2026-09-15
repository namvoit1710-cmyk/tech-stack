"""SA-1736: NodeDefinition.kind + collection heal-on-read + kind validation."""

from app.layer1_domain.entities.node_definition import NodeDefinition


def test_kind_heals_on_read_from_verb():
    nd = NodeDefinition(name="Google Search", base_worker_id="http-worker",
                        locked_defaults={"method": "GET"})
    assert nd.kind == "read"        # healed, no migration


def test_collection_defaults_to_my_nodes():
    nd = NodeDefinition(name="X", base_worker_id="http-worker", locked_defaults={"method": "POST"})
    assert nd.collection == "My Nodes"
    assert nd.kind == "action"


def test_explicit_kind_kept():
    nd = NodeDefinition(name="X", base_worker_id="http-worker", kind="util")
    assert nd.kind == "util"


def test_validate_rejects_bad_kind():
    nd = NodeDefinition(name="X", base_worker_id="http-worker", kind="bogus")
    assert any("kind" in e for e in nd.validate())
