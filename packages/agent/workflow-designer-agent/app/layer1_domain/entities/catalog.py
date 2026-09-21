from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CatalogNode:
    """A node type available in the workflow palette (summary view)."""

    id: str
    name: str
    node_type: str
    runtime: str = ""
    description: str = ""
    icon: str = ""
    color: str = ""
    # Browse/disambiguation taxonomy the control plane already computes and returns
    # (e.g. category="Core.human", kind="human", collection="Core"). Forwarded to the
    # model so look-alike nodes (e.g. the "pause for a human" cluster) are separable at
    # pick time — the pick runs on the summary, before any config schema is fetched.
    category: str = ""
    kind: str = ""
    collection: str = ""
    tags: list[str] = field(default_factory=list)
    in_ports: list[str] = field(default_factory=list)
    out_ports: list[str] = field(default_factory=list)


@dataclass
class CatalogWorker:
    """A registered worker/agent that can back a TASK node (its id is a valid worker_type)."""

    id: str
    name: str
    capabilities: list[str] = field(default_factory=list)


@dataclass
class CatalogField:
    """One config field of a node type, as advertised by the live catalog.

    Everything here is discovered from the control-plane node-catalog detail response —
    nothing about node config is hardcoded in this agent. ``options`` is populated for
    select-like controls; ``fields`` carries the nested schema for object/array fields.
    """

    key: str
    type: str = ""          # string | number | boolean | object | array
    required: bool = False
    control: str = ""       # e.g. TextareaControl, SelectControl, CodeControl
    options: list[str] = field(default_factory=list)
    # Human-readable field help the control-plane renders into the form schema
    # (label = wrapper_props.label, placeholder = control_props.placeholder). The raw
    # ``key`` (e.g. "trigger_type") is machine-y; the label ("Trigger Type") is what the
    # model needs to fill config correctly. (The spec's free-text field description is
    # dropped upstream by the control-plane form renderer — a separate follow-up.)
    label: str = ""
    placeholder: str = ""
    fields: list["CatalogField"] = field(default_factory=list)


@dataclass
class CatalogNodeSchema:
    """The live config + output + port contract for a single node type.

    Fetched from ``GET/POST /api/v2/node-catalog`` details. The LLM uses this to fill each
    node's ``config`` and to build ``{{$<node>.output.<field>}}`` data bindings, so the
    agent never needs a hardcoded per-node-type config table.
    """

    id: str
    node_type: str
    name: str = ""
    description: str = ""
    config_fields: list[CatalogField] = field(default_factory=list)
    output_fields: list[str] = field(default_factory=list)
    in_ports: list[str] = field(default_factory=list)
    out_ports: list[str] = field(default_factory=list)
