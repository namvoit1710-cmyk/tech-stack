"""Pure pydantic test: controller request models accept kind + collection.

Runnable without app bootstrap:
    python -m pytest tests/unit/test_node_definition_request_kind.py -q --noconftest
"""

from app.layer3_adapters.controllers.restful.v1.node_definition_controller import (
    CreateNodeDefinitionRequest,
    PatchNodeDefinitionRequest,
)


def test_create_request_accepts_kind_collection():
    req = CreateNodeDefinitionRequest(
        tenant_id="t1",
        name="X",
        base_worker_id="http-worker",
        kind="read",
        collection="Google",
    )
    d = req.model_dump()
    assert d["kind"] == "read"
    assert d["collection"] == "Google"


def test_patch_request_accepts_kind_collection():
    req = PatchNodeDefinitionRequest(kind="util", collection="Mine")
    d = req.model_dump(exclude_none=True)
    assert d["kind"] == "util"
    assert d["collection"] == "Mine"
