from __future__ import annotations

from typing import Any

from agent_sdk import tool

from app.layer1_domain.entities.catalog import CatalogField, CatalogNodeSchema
from app.layer2_application.interfaces.catalog_provider_interface import ICatalogProvider


def _field_to_dict(f: CatalogField) -> dict[str, Any]:
    out: dict[str, Any] = {"key": f.key, "type": f.type, "required": f.required}
    if f.control:
        out["control"] = f.control
    if f.options:
        out["options"] = f.options
    # Human-readable help (only when present) so the model fills config from the label
    # ("Trigger Type"), not just the machine key ("trigger_type"). Kept conditional so a
    # help-less field serialises to the same minimal shape as before.
    if f.label:
        out["label"] = f.label
    if f.placeholder:
        out["placeholder"] = f.placeholder
    if f.fields:
        out["fields"] = [_field_to_dict(sub) for sub in f.fields]
    return out


def _schema_to_dict(s: CatalogNodeSchema) -> dict[str, Any]:
    return {
        "id": s.id,
        "node_type": s.node_type,
        "name": s.name,
        "description": s.description,
        "config_fields": [_field_to_dict(f) for f in s.config_fields],
        "output_fields": s.output_fields,
        "in_ports": s.in_ports,
        "out_ports": s.out_ports,
    }


def build_catalog_tools(catalog: ICatalogProvider) -> list[Any]:
    """Introspection tools bound to the live control-plane catalog (deps via closure).

    The LLM calls these to DISCOVER the palette and each node's real config schema at run
    time — the agent hardcodes no node types or config fields.
    """

    @tool("list_node_types", internal=True)
    async def list_node_types() -> dict:
        """List every node type available in the live workflow palette.

        Call this FIRST to discover what nodes exist — it is the ONLY source of valid node
        types; the designer rejects any node whose type/worker_type is not listed here.
        Returns each node's ``id`` (pass it to get_node_schema, and for a worker-backed node
        this id is its "worker_type"), its ``node_type`` (use as a node's "type"), a name, a
        short description, its ``runtime``, its ``in_ports``/``out_ports`` (so you can pick
        nodes that actually connect), and its browse taxonomy — ``category``, ``kind``,
        ``collection``, ``tags`` — which distinguishes look-alike nodes (e.g. a control-flow
        node vs a "pause for a human" node) at pick time. For each node's config, call
        get_node_schema next.

        Returns:
            {"node_types": [{"id", "node_type", "name", "description", "runtime",
                             "category", "kind", "collection", "tags",
                             "in_ports", "out_ports"}]}
        """
        nodes = await catalog.nodes()
        return {
            "node_types": [
                {
                    "id": n.id,
                    "node_type": n.node_type,
                    "name": n.name,
                    "description": n.description,
                    "runtime": n.runtime,
                    "category": n.category,
                    "kind": n.kind,
                    "collection": n.collection,
                    "tags": n.tags,
                    "in_ports": n.in_ports,
                    "out_ports": n.out_ports,
                }
                for n in nodes
            ]
        }

    @tool("get_node_schema", internal=True)
    async def get_node_schema(node_ids: list[str]) -> dict:
        """Fetch the real config + output + port schema for specific node types.

        Pass the ``id`` values (or node_type names) from list_node_types. Use the returned
        ``config_fields`` (key/type/required/control/options, plus ``label``/``placeholder``
        help when present) to fill each node's "config", and ``output_fields`` to build data
        bindings like ``{{$<node_id>.output.<field>}}``.
        Always call this for every node type you plan to use before creating the workflow.

        Args:
            node_ids: Catalog ids or node_type names to fetch schemas for.
        Returns:
            {"schemas": [{"id","node_type","config_fields","output_fields","in_ports","out_ports"}],
             "missing": [ids with no schema]}
        """
        wanted = [str(x) for x in (node_ids or []) if str(x).strip()]
        schemas = await catalog.node_schemas(wanted)
        found = {s.id for s in schemas} | {s.node_type.upper() for s in schemas}
        missing = [x for x in wanted if x not in found and x.upper() not in found]
        return {"schemas": [_schema_to_dict(s) for s in schemas], "missing": missing}

    return [list_node_types, get_node_schema]
