from app.layer2_application.contracts.node_definition_dto import NodeDefinitionSummary


def test_summary_has_kind_collection():
    s = NodeDefinitionSummary(id="1", provider="business", nodeType="task",
                              runtime="worker", kind="read", collection="Google")
    dumped = s.model_dump(by_alias=True)
    assert dumped["kind"] == "read"
    assert dumped["collection"] == "Google"


def test_summary_kind_collection_defaults():
    s = NodeDefinitionSummary(id="1", provider="business", nodeType="task", runtime="worker")
    d = s.model_dump(by_alias=True)
    assert d["kind"] == "action"
    assert d["collection"] == "My Nodes"
