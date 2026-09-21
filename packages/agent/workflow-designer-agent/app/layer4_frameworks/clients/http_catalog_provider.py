from __future__ import annotations

import logging
from typing import Any

import httpx

from app.layer1_domain.entities.catalog import (
    CatalogField,
    CatalogNode,
    CatalogNodeSchema,
    CatalogWorker,
)
from app.layer2_application.interfaces.catalog_provider_interface import ICatalogProvider

logger = logging.getLogger(__name__)


class HttpCatalogProvider(ICatalogProvider):
    """Fetches the live node catalog (control-plane) and worker list (agent-registry).

    ``nodes`` returns the palette summary (id, node_type, ports); ``node_schemas`` batch-
    fetches the full per-node config + output + port contract from the detail endpoint.
    All node metadata comes from the API — nothing is hardcoded here. Results are cached;
    an unreachable source degrades to an empty list so the caller can react gracefully.
    """

    def __init__(
        self,
        control_plane_base: str,
        registry_base: str = "",
        tenant_id: str = "system",
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._cp = (control_plane_base or "").rstrip("/")
        self._reg = (registry_base or "").rstrip("/")
        self._tenant = tenant_id or "system"
        self._timeout = timeout
        self._client = client
        self._nodes_cache: list[CatalogNode] | None = None
        self._workers_cache: list[CatalogWorker] | None = None
        self._schema_cache: dict[str, CatalogNodeSchema] = {}

    async def nodes(self) -> list[CatalogNode]:
        if self._nodes_cache is None:
            data = await self._get(self._cp, f"/api/v2/node-catalog?tenant_id={self._tenant}")
            self._nodes_cache = self._parse_nodes(data)
            logger.info("catalog: %d node types loaded", len(self._nodes_cache))
        return self._nodes_cache

    async def node_schemas(self, refs: list[str]) -> list[CatalogNodeSchema]:
        """Batch-fetch full config/output/port schemas for the given catalog ids/refs."""
        wanted = [r for r in dict.fromkeys(refs) if r]  # de-dupe, keep order
        missing = [r for r in wanted if r not in self._schema_cache]
        if missing:
            data = await self._post(
                self._cp,
                "/api/v2/node-catalog/details",
                {"refs": missing, "tenant_id": self._tenant},
            )
            for detail in self._details_of(data):
                schema = self._parse_schema(detail)
                if schema.id:
                    self._schema_cache[schema.id] = schema
                    self._schema_cache[schema.node_type.upper()] = schema
        out: list[CatalogNodeSchema] = []
        for ref in wanted:
            schema = self._schema_cache.get(ref) or self._schema_cache.get(ref.upper())
            if schema is not None:
                out.append(schema)
        return out

    async def workers(self) -> list[CatalogWorker]:
        if self._workers_cache is None:
            data = await self._get(self._reg, "/api/v1/agents/available")
            self._workers_cache = self._parse_workers(data)
            logger.info("catalog: %d workers loaded", len(self._workers_cache))
        return self._workers_cache

    # --- pure parsers (unit-tested without a network) ---

    @staticmethod
    def _unwrap(data: Any) -> dict:
        """Unwrap the {"status":"ok","data":{...}} envelope (or pass a flat dict through)."""
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            return data["data"]
        return data if isinstance(data, dict) else {}

    @classmethod
    def _details_of(cls, data: Any) -> list[dict]:
        container = cls._unwrap(data)
        details = container.get("details")
        return [d for d in details if isinstance(d, dict)] if isinstance(details, list) else []

    @staticmethod
    def _ports(container: dict, side: str) -> list[str]:
        """Read port ids for ``side`` ('in'|'out'), tolerating both key spellings."""
        ports = container.get("ports") or {}
        if not isinstance(ports, dict):
            return []
        key_long = "inputs" if side == "in" else "outputs"
        entries = ports.get(key_long) or ports.get(side) or []
        return [p.get("id") for p in entries if isinstance(p, dict) and p.get("id")]

    @classmethod
    def _parse_nodes(cls, data: Any) -> list[CatalogNode]:
        container = cls._unwrap(data)
        out: list[CatalogNode] = []
        for d in container.get("definitions", []) or []:
            if not isinstance(d, dict) or not d.get("id"):
                continue
            tags = [str(t) for t in (d.get("tags") or []) if isinstance(t, (str, int))]
            out.append(CatalogNode(
                id=str(d.get("id")),
                name=str(d.get("name") or d.get("id")),
                node_type=str(d.get("node_type") or d.get("nodeType") or ""),
                runtime=str(d.get("runtime") or ""),
                description=str(d.get("description") or ""),
                # Display fields the control plane persists verbatim (defaults mirror the
                # node-catalog DTO / worker registry), so an agent-authored node looks the
                # same as one dragged from the palette.
                icon=str(d.get("icon") or "Cog"),
                color=str(d.get("color") or "#3B82F6"),
                # Browse/disambiguation taxonomy the control plane returns per node.
                category=str(d.get("category") or ""),
                kind=str(d.get("kind") or ""),
                collection=str(d.get("collection") or ""),
                tags=tags,
                in_ports=cls._ports(d, "in"),
                out_ports=cls._ports(d, "out"),
            ))
        return out

    @classmethod
    def _parse_schema(cls, detail: dict) -> CatalogNodeSchema:
        input_schema = detail.get("input_schema") or detail.get("inputSchema") or []
        output_schema = detail.get("output_schema") or detail.get("outputSchema") or []
        config_fields = [cls._parse_field(f) for f in input_schema if isinstance(f, dict)]
        output_fields = [str(f.get("key")) for f in output_schema
                         if isinstance(f, dict) and f.get("key")]
        return CatalogNodeSchema(
            id=str(detail.get("id") or ""),
            node_type=str(detail.get("node_type") or detail.get("nodeType") or ""),
            name=str(detail.get("name") or ""),
            description=str(detail.get("description") or ""),
            config_fields=config_fields,
            output_fields=output_fields,
            in_ports=cls._ports(detail, "in"),
            out_ports=cls._ports(detail, "out"),
        )

    @classmethod
    def _parse_field(cls, f: dict) -> CatalogField:
        fc = f.get("field_config") or f.get("fieldConfig") or {}
        control = str(fc.get("field_control") or fc.get("fieldControl") or "")
        rules = fc.get("rules") or []
        required = any(
            isinstance(r, dict) and r.get("method") == "required" for r in rules
        )
        control_props = fc.get("control_props") or fc.get("controlProps") or {}
        options: list[str] = []
        for opt in (control_props.get("options") or []):
            if isinstance(opt, dict) and opt.get("value") is not None:
                options.append(str(opt.get("value")))
        # Human-readable help: label from the form-item wrapper, placeholder from the control.
        wrapper_props = fc.get("wrapper_props") or fc.get("wrapperProps") or {}
        label = str(wrapper_props.get("label") or "") if isinstance(wrapper_props, dict) else ""
        placeholder = str(control_props.get("placeholder") or "") if isinstance(control_props, dict) else ""
        nested = [cls._parse_field(sub) for sub in (f.get("fields") or [])
                  if isinstance(sub, dict)]
        return CatalogField(
            key=str(f.get("key") or ""),
            type=str(f.get("output_type") or f.get("outputType") or ""),
            required=required,
            control=control,
            options=options,
            label=label,
            placeholder=placeholder,
            fields=nested,
        )

    @classmethod
    def _parse_workers(cls, data: Any) -> list[CatalogWorker]:
        container = cls._unwrap(data)
        out: list[CatalogWorker] = []
        for a in container.get("agents", []) or []:
            if not isinstance(a, dict) or not a.get("id"):
                continue
            caps = [c for c in (a.get("capabilities") or []) if isinstance(c, str)]
            out.append(CatalogWorker(id=str(a.get("id")), name=str(a.get("name") or a.get("id")), capabilities=caps))
        return out

    async def _get(self, base: str, path: str) -> Any:
        return await self._request("GET", base, path, None)

    async def _post(self, base: str, path: str, payload: dict) -> Any:
        return await self._request("POST", base, path, payload)

    async def _request(self, method: str, base: str, path: str, payload: dict | None) -> Any:
        if not base.startswith(("http://", "https://")):
            return None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        owns = self._client is None
        try:
            if method == "POST":
                resp = await client.post(f"{base}{path}", json=payload or {})
            else:
                resp = await client.get(f"{base}{path}")
            if resp.status_code != 200:
                logger.info("catalog %s %s -> HTTP %s", method, path, resp.status_code)
                return None
            return resp.json()
        except Exception as exc:  # unreachable / non-JSON -> graceful empty
            logger.info("catalog %s %s failed: %s", method, path, exc)
            return None
        finally:
            if owns:
                await client.aclose()
