from __future__ import annotations

from app.layer1_domain.entities.catalog import (
    CatalogField,
    CatalogNode,
    CatalogNodeSchema,
)
from app.layer4_frameworks.tools.catalog_tools import _field_to_dict, build_catalog_tools


class FakeCatalog:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def nodes(self):
        return [
            CatalogNode(id="builtin:prompt", name="Prompt", node_type="PROMPT",
                        description="Emit text", out_ports=["out"]),
            CatalogNode(id="worker:http_request", name="HTTP Request", node_type="TASK",
                        description="Call an API", out_ports=["success"],
                        category="Integration.action", kind="action",
                        collection="Integration", tags=["http", "api"]),
        ]

    async def node_schemas(self, refs):
        self.asked = list(refs)
        return [
            CatalogNodeSchema(
                id="builtin:prompt", node_type="PROMPT",
                config_fields=[CatalogField(key="content", type="string", required=True,
                                            control="TextareaControl")],
                output_fields=["content"], in_ports=["in_default"], out_ports=["out"],
            )
        ]

    async def workers(self):
        return []


def _tools():
    return {t.name: t for t in build_catalog_tools(FakeCatalog())}


async def test_list_node_types_returns_live_palette() -> None:
    result = await _tools()["list_node_types"].ainvoke({})
    types = {n["node_type"] for n in result["node_types"]}
    assert types == {"PROMPT", "TASK"}
    ids = {n["id"] for n in result["node_types"]}
    assert "worker:http_request" in ids
    # Palette entries carry enough to pick a connectable node: ports + runtime are surfaced.
    http = next(n for n in result["node_types"] if n["id"] == "worker:http_request")
    assert http["out_ports"] == ["success"]
    assert "in_ports" in http and "runtime" in http


async def test_list_node_types_forwards_browse_taxonomy() -> None:
    # SA-2210: category/kind/collection/tags reach the model so look-alike nodes are
    # separable at pick time (the pick runs on the summary, before get_node_schema).
    result = await _tools()["list_node_types"].ainvoke({})
    http = next(n for n in result["node_types"] if n["id"] == "worker:http_request")
    assert http["category"] == "Integration.action"
    assert http["kind"] == "action"
    assert http["collection"] == "Integration"
    assert http["tags"] == ["http", "api"]


def test_field_to_dict_includes_help_when_present() -> None:
    # SA-2210: label/placeholder give the model human help beyond the machine key.
    d = _field_to_dict(CatalogField(key="trigger_type", type="string", required=True,
                                    control="SelectControl", label="Trigger Type",
                                    placeholder="Select trigger type"))
    assert d["label"] == "Trigger Type"
    assert d["placeholder"] == "Select trigger type"


def test_field_to_dict_omits_help_when_absent() -> None:
    # A help-less field serialises to the same minimal shape as before the change.
    d = _field_to_dict(CatalogField(key="content", type="string", required=True,
                                    control="TextareaControl"))
    assert d == {"key": "content", "type": "string", "required": True,
                 "control": "TextareaControl"}


async def test_get_node_schema_returns_config_fields_and_reports_missing() -> None:
    result = await _tools()["get_node_schema"].ainvoke(
        {"node_ids": ["builtin:prompt", "builtin:nope"]}
    )
    assert len(result["schemas"]) == 1
    schema = result["schemas"][0]
    assert schema["node_type"] == "PROMPT"
    assert schema["config_fields"][0] == {
        "key": "content", "type": "string", "required": True, "control": "TextareaControl"
    }
    assert schema["output_fields"] == ["content"]
    assert result["missing"] == ["builtin:nope"]
