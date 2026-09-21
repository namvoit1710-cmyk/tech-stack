import asyncio
import json
import os
import re
from importlib.resources import files
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request

import litellm
from litellm._logging import verbose_logger
from litellm.litellm_core_utils.get_blog_posts import (
    BlogPost,
    BlogPostsResponse,
    GetBlogPosts,
    get_blog_posts,
)
from litellm.proxy._types import (
    CommonProxyErrors,
)
from litellm.proxy.utils import get_custom_url
from litellm.repositories.table_repositories import ClaudeCodePluginRepository
from litellm.types.agents import AgentCard
from litellm.types.mcp import MCPPublicServer
from litellm.types.proxy.management_endpoints.model_management_endpoints import (
    ModelGroupInfoProxy,
)
from litellm.types.proxy.public_endpoints.public_endpoints import (
    AgentCreateInfo,
    ProviderCreateInfo,
    PublicModelHubInfo,
    SupportedEndpointsResponse,
)
from litellm.types.utils import LlmProviders

router = APIRouter()


# ---------------------------------------------------------------------------
# /public/endpoints — helpers
# ---------------------------------------------------------------------------

_ENDPOINT_METADATA: Dict[str, Dict[str, str]] = {
    "chat_completions": {"label": "Chat Completions", "endpoint": "/chat/completions"},
    "messages": {"label": "Messages", "endpoint": "/messages"},
    "responses": {"label": "Responses", "endpoint": "/responses"},
    "embeddings": {"label": "Embeddings", "endpoint": "/embeddings"},
    "image_generations": {
        "label": "Image Generations",
        "endpoint": "/images/generations",
    },
    "audio_transcriptions": {
        "label": "Audio Transcriptions",
        "endpoint": "/audio/transcriptions",
    },
    "audio_speech": {"label": "Audio Speech", "endpoint": "/audio/speech"},
    "moderations": {"label": "Moderations", "endpoint": "/moderations"},
    "batches": {"label": "Batches", "endpoint": "/batches"},
    "rerank": {"label": "Rerank", "endpoint": "/rerank"},
    "ocr": {"label": "OCR", "endpoint": "/ocr"},
    "search": {"label": "Search", "endpoint": "/search"},
    "skills": {"label": "Skills", "endpoint": "/skills"},
    "interactions": {"label": "Interactions", "endpoint": "/interactions"},
    "a2a": {"label": "A2A (Agent Gateway)", "endpoint": "/a2a/{agent}/message/send"},
    "container": {"label": "Containers", "endpoint": "/containers"},
    "container_files": {
        "label": "Container Files",
        "endpoint": "/containers/{id}/files",
    },
    "compact": {"label": "Compact", "endpoint": "/responses/compact"},
    "files": {"label": "Files", "endpoint": "/files"},
    "image_edits": {"label": "Image Edits", "endpoint": "/images/edits"},
    "vector_stores_create": {
        "label": "Vector Stores (Create)",
        "endpoint": "/vector_stores",
    },
    "vector_stores_search": {
        "label": "Vector Stores (Search)",
        "endpoint": "/vector_stores/{id}/search",
    },
    "vector_store_files": {
        "label": "Vector Store Files",
        "endpoint": "/vector_stores/{id}/files",
    },
    "video_generations": {
        "label": "Video Generations",
        "endpoint": "/videos/generations",
    },
    "assistants": {"label": "Assistants", "endpoint": "/assistants"},
    "fine_tuning": {"label": "Fine Tuning", "endpoint": "/fine_tuning/jobs"},
    "text_completion": {"label": "Text Completion", "endpoint": "/completions"},
    "realtime": {"label": "Realtime", "endpoint": "/realtime"},
    "count_tokens": {"label": "Count Tokens", "endpoint": "/utils/token_counter"},
    "image_variations": {"label": "Image Variations", "endpoint": "/images/variations"},
    "generateContent": {"label": "Generate Content", "endpoint": "/generateContent"},
    "bedrock_invoke": {"label": "Bedrock Invoke", "endpoint": "/bedrock/invoke"},
    "bedrock_converse": {"label": "Bedrock Converse", "endpoint": "/bedrock/converse"},
    "rag_ingest": {"label": "RAG Ingest", "endpoint": "/rag/ingest"},
    "rag_query": {"label": "RAG Query", "endpoint": "/rag/query"},
}

_SLUG_SUFFIX_RE = re.compile(r"\s*\(`[^`]+`\)\s*$")

# Loaded once on first request; never invalidated (local file, no TTL needed).
_cached_endpoints: Optional[SupportedEndpointsResponse] = None


def _clean_display_name(raw: str) -> str:
    return _SLUG_SUFFIX_RE.sub("", raw).strip()


def _build_endpoints(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Transform raw provider_endpoints_support_backup.json into the response shape."""
    providers: Dict[str, Any] = raw.get("providers", {})

    # Collect endpoint keys in insertion order (union across all providers).
    seen: set = set()
    all_keys: List[str] = []
    for provider_data in providers.values():
        for key in provider_data.get("endpoints", {}):
            if key not in seen:
                seen.add(key)
                all_keys.append(key)

    result: List[Dict[str, Any]] = []
    for key in all_keys:
        meta = _ENDPOINT_METADATA.get(key)
        label = meta["label"] if meta else key.replace("_", " ").title()
        path = meta["endpoint"] if meta else "/" + key.replace("_", "/")

        supporting: List[Dict[str, str]] = [
            {
                "slug": slug,
                "display_name": _clean_display_name(pd.get("display_name", slug)),
            }
            for slug, pd in providers.items()
            if pd.get("endpoints", {}).get(key)
        ]
        result.append({"key": key, "label": label, "endpoint": path, "providers": supporting})

    return result


def _load_endpoints() -> List[Dict[str, Any]]:
    raw = json.loads(files("litellm").joinpath("provider_endpoints_support_backup.json").read_text(encoding="utf-8"))
    return _build_endpoints(raw)


# ---------------------------------------------------------------------------


@router.get(
    "/public/model_hub",
    tags=["public", "model management"],
    response_model=List[ModelGroupInfoProxy],
)
async def public_model_hub():
    import litellm
    from litellm.proxy.health_endpoints._health_endpoints import (
        _convert_health_check_to_dict,
    )
    from litellm.proxy.proxy_server import (
        _get_model_group_info,
        llm_router,
        prisma_client,
    )

    if llm_router is None:
        raise HTTPException(status_code=400, detail=CommonProxyErrors.no_llm_router.value)

    model_groups: List[ModelGroupInfoProxy] = []
    if litellm.public_model_groups is not None:
        model_groups = _get_model_group_info(
            llm_router=llm_router,
            all_models_str=litellm.public_model_groups,
            model_group=None,
        )

    # Fetch health check information if available
    health_checks_map = {}
    if prisma_client is not None:
        try:
            latest_checks = await prisma_client.get_all_latest_health_checks()
            for check in latest_checks:
                key = check.model_id if check.model_id else check.model_name
                if key:
                    health_check_dict = _convert_health_check_to_dict(check)
                    health_checks_map[key] = health_check_dict
                    if check.model_name:
                        health_checks_map[check.model_name] = health_check_dict
        except Exception:
            pass

    for model_group in model_groups:
        health_info = health_checks_map.get(model_group.model_group)
        if health_info:
            model_group.health_status = health_info.get("status")
            model_group.health_response_time = health_info.get("response_time_ms")
            model_group.health_checked_at = health_info.get("checked_at")

    return model_groups


@router.get(
    "/public/agent_hub",
    tags=["[beta] Agents", "public"],
    response_model=List[AgentCard],
)
async def get_agents(request: Request):
    import litellm
    from litellm.proxy.agent_endpoints.agent_registry import global_agent_registry

    agents = global_agent_registry.get_public_agent_list()

    if litellm.public_agent_groups is None:
        return []

    return [
        {
            **(agent.agent_card_params or {}),
            "url": get_custom_url(str(request.base_url), route=f"a2a/{agent.agent_id}"),
        }
        for agent in agents
        if agent.agent_id in litellm.public_agent_groups
    ]


@router.get(
    "/public/mcp_hub",
    tags=["[beta] MCP", "public"],
    response_model=List[MCPPublicServer],
)
async def get_mcp_servers():
    from litellm.proxy._experimental.mcp_server.mcp_server_manager import (
        global_mcp_server_manager,
    )

    public_mcp_servers = global_mcp_server_manager.get_public_mcp_servers()
    return [
        MCPPublicServer(
            **server.model_dump(),
        )
        for server in public_mcp_servers
    ]


@router.get(
    "/public/skill_hub",
    tags=["public", "Claude Code Marketplace"],
)
async def public_skill_hub():
    """Return enabled (public) Claude Code skills — no auth required."""
    from litellm.proxy.anthropic_endpoints.claude_code_endpoints.claude_code_marketplace import (
        _get_prisma_client,
    )
    from litellm.types.proxy.claude_code_endpoints import (
        ListPluginsResponse,
        PluginListItem,
    )

    try:
        prisma_client = await _get_prisma_client()
        plugins = await ClaudeCodePluginRepository(prisma_client).table.find_many(where={"enabled": True})
        items = []
        for plugin in plugins:
            raw = plugin.manifest_json or {}
            manifest = json.loads(raw) if isinstance(raw, str) else raw
            items.append(
                PluginListItem(
                    id=plugin.id,
                    name=plugin.name,
                    enabled=plugin.enabled,
                    created_at=str(plugin.created_at) if plugin.created_at else None,
                    updated_at=str(plugin.updated_at) if plugin.updated_at else None,
                    source=manifest.get("source", {}),
                    description=manifest.get("description"),
                    version=manifest.get("version"),
                    category=manifest.get("category"),
                    keywords=manifest.get("keywords"),
                    author=manifest.get("author"),
                    homepage=manifest.get("homepage"),
                    domain=manifest.get("domain"),
                    namespace=manifest.get("namespace"),
                )
            )
        return ListPluginsResponse(plugins=items, count=len(items))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/public/model_hub/info",
    tags=["public", "model management"],
    response_model=PublicModelHubInfo,
)
async def public_model_hub_info():
    import litellm
    from litellm.proxy.proxy_server import _title, version

    try:
        from litellm_enterprise.proxy.proxy_server import EnterpriseProxyConfig

        custom_docs_description = EnterpriseProxyConfig.get_custom_docs_description()
    except Exception:
        custom_docs_description = None

    return PublicModelHubInfo(
        docs_title=_title,
        custom_docs_description=custom_docs_description,
        litellm_version=version,
        useful_links=litellm.public_model_groups_links,
    )


@router.get(
    "/public/providers",
    tags=["public", "providers"],
    response_model=List[str],
)
async def get_supported_providers() -> List[str]:
    """
    Return a sorted list of all providers supported by LiteLLM.
    """

    return sorted(provider.value for provider in LlmProviders)


@router.get(
    "/public/providers/fields",
    tags=["public", "providers"],
    response_model=List[ProviderCreateInfo],
)
async def get_provider_fields() -> List[ProviderCreateInfo]:
    """
    Return provider metadata required by the dashboard create-model flow.
    """

    provider_create_fields_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "proxy",
        "public_endpoints",
        "provider_create_fields.json",
    )

    with open(provider_create_fields_path, "r") as f:
        provider_create_fields = json.load(f)

    return provider_create_fields


@router.get(
    "/public/litellm_model_cost_map",
    tags=["public", "model management"],
)
async def get_litellm_model_cost_map():
    """
    Public endpoint to get the LiteLLM model cost map.
    Returns pricing information for all supported models.
    """
    import litellm

    try:
        _model_cost_map = litellm.model_cost
        return _model_cost_map
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Internal Server Error ({str(e)})",
        )


# --- Model catalog: one call = cost-map attrs + supported params + param ranges ---
# Cached; rebuilt when the underlying cost map changes (id/len key). The per-model
# supported_openai_params computation happens once at build time, not per request.
_MODEL_CATALOG_CACHE: Dict[str, Any] = {"key": None, "data": None}


# Only compute supported_openai_params for providers whose config init is a pure,
# fast, offline lookup. Many of LiteLLM's 120+ providers (e.g. github_copilot, the
# Codex/ChatGPT provider) trigger interactive device-code auth or network calls on
# init that can HANG indefinitely — so anything not on this allowlist reports [] here.
# Callers wanting the exact param list for such a model can use
# /utils/supported_openai_params?model=... on demand (their explicit choice).
_SAFE_PARAM_PROVIDERS = {
    "openai", "azure", "azure_ai", "text-completion-openai",
    "anthropic", "bedrock", "vertex_ai", "gemini",
    "cohere", "cohere_chat", "mistral", "groq", "deepseek", "xai",
    "openrouter", "together_ai", "fireworks_ai", "perplexity", "ollama",
    "deepinfra", "ai21", "nvidia_nim", "cerebras", "sambanova",
}


def _supported_params_for(provider: Optional[str], model: str, memo: Dict[str, Any]) -> List[str]:
    if not provider or provider not in _SAFE_PARAM_PROVIDERS:
        return []
    if provider in memo:
        return memo[provider]
    try:
        val = litellm.get_supported_openai_params(model=model, custom_llm_provider=provider) or []
    except Exception:
        val = []
    memo[provider] = val
    return val


def _catalog_entry(model_name: str, attrs: Dict[str, Any], memo: Dict[str, Any]) -> Dict[str, Any]:
    from litellm.proxy.public_endpoints.model_param_ranges import (
        get_param_ranges_for_model,
    )

    provider = attrs.get("litellm_provider")
    entry: Dict[str, Any] = dict(attrs)
    entry["supported_openai_params"] = _supported_params_for(provider, model_name, memo)
    entry["param_ranges"] = get_param_ranges_for_model(model_name, provider or "")
    return entry


def _is_real_model(name: str, attrs: Dict[str, Any]) -> bool:
    # ``litellm.model_cost`` embeds a docs/template row ("sample_spec") whose
    # litellm_provider is the placeholder "one of https://docs.litellm.ai/...".
    # Exclude it (and any similar placeholder) so the catalog is real models only.
    if name == "sample_spec":
        return False
    provider = attrs.get("litellm_provider")
    if isinstance(provider, str) and provider.startswith("one of"):
        return False
    return True


def _build_model_catalog() -> Dict[str, Any]:
    model_cost = litellm.model_cost
    cache_key = (id(model_cost), len(model_cost))
    if _MODEL_CATALOG_CACHE["key"] == cache_key and _MODEL_CATALOG_CACHE["data"] is not None:
        return _MODEL_CATALOG_CACHE["data"]

    # supported_openai_params memoized per provider (a few dozen calls, not ~3k).
    memo: Dict[str, Any] = {}
    catalog: Dict[str, Any] = {
        name: _catalog_entry(name, attrs, memo)
        for name, attrs in model_cost.items()
        if isinstance(attrs, dict) and _is_real_model(name, attrs)
    }
    _MODEL_CATALOG_CACHE["key"] = cache_key
    _MODEL_CATALOG_CACHE["data"] = catalog
    return catalog


@router.get("/public/model_catalog", tags=["public", "model management"])
async def get_model_catalog(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    mode: Optional[str] = None,
):
    """
    One-call model catalog: every known model merged with its cost-map attributes
    (provider, mode, token limits, per-token costs, supports_* flags), its
    ``supported_openai_params`` (does it accept temperature/top_p/…), and curated
    ``param_ranges`` (min/max/default per sampling param). Public, read-only.

    Optional filters:
      * ``?model=gpt-4o``      — a single model (exact supported_openai_params for it)
      * ``?provider=openai``   — all models for a provider
      * ``?mode=chat``         — all models of a mode (chat, embedding, image_generation, …)

    Note: in the bulk (unfiltered/provider/mode) view, ``supported_openai_params`` is
    memoized per provider for speed; for the exact per-model list use ``?model=`` or
    /utils/supported_openai_params. ``param_ranges`` is always model-accurate.
    """
    try:
        # Fast path: a single model builds only that entry (one guarded param lookup),
        # never the full ~3k-model catalog.
        if model is not None:
            attrs = litellm.model_cost.get(model)
            if not isinstance(attrs, dict) or not _is_real_model(model, attrs):
                return {}
            entry = await asyncio.to_thread(_catalog_entry, model, dict(attrs), {})
            return {model: entry}

        catalog = await asyncio.to_thread(_build_model_catalog)
        if provider is not None:
            catalog = {k: v for k, v in catalog.items() if v.get("litellm_provider") == provider}
        if mode is not None:
            catalog = {k: v for k, v in catalog.items() if v.get("mode") == mode}
        return catalog
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal Server Error ({str(e)})")


@router.get("/public/model_param_ranges", tags=["public", "model management"])
async def get_model_param_ranges(
    model: Optional[str] = None,
    provider: Optional[str] = None,
):
    """
    Curated sampling-parameter ranges (temperature, top_p, …) as the raw building
    block behind /public/model_catalog. Public, read-only.

      * no args           — full provider map + model overrides
      * ``?provider=openai`` — one provider's ranges
      * ``?model=gpt-4o``  — resolved ranges for a specific model (provider + overrides)
    """
    from litellm.proxy.public_endpoints.model_param_ranges import (
        MODEL_PARAM_OVERRIDES,
        PROVIDER_PARAM_RANGES,
        get_param_ranges_for_model,
    )

    try:
        if model is not None:
            prov = provider
            if prov is None:
                try:
                    _, prov, _, _ = litellm.get_llm_provider(model=model)
                except Exception:
                    prov = model.split("/", 1)[0] if "/" in model else ""
            return {model: get_param_ranges_for_model(model, prov or "")}
        if provider is not None:
            return {"providers": {provider: PROVIDER_PARAM_RANGES.get(provider, {})}}
        return {"providers": PROVIDER_PARAM_RANGES, "model_overrides": MODEL_PARAM_OVERRIDES}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal Server Error ({str(e)})")


# Attributes copied onto each grouped model entry (alongside the flattened param ranges).
_MODELS_BY_PROVIDER_ATTRS = (
    "mode",
    "max_input_tokens",
    "max_output_tokens",
    "max_tokens",
    "input_cost_per_token",
    "output_cost_per_token",
    "supports_function_calling",
    "supports_vision",
    "supports_tool_choice",
    "supports_reasoning",
)


@router.get("/public/models_by_provider", tags=["public", "model management"])
async def get_models_by_provider(
    provider: Optional[str] = None,
    mode: Optional[str] = None,
):
    """
    Model info **grouped by provider** — one call, shaped as a dict of
    ``provider -> [ {model, temperature, top_p, ...}, ... ]``:

        {
          "openai": [
            {"model": "gpt-4o",
             "temperature": {"min": 0, "max": 2, "default": 1},
             "top_p": {"min": 0, "max": 1, "default": 1},
             "mode": "chat", "max_input_tokens": 128000,
             "supports_vision": true,
             "supported_openai_params": ["temperature","top_p", ...]},
            {"model": "o1", "temperature": {"supported": false}, ...}
          ],
          "anthropic": [ {"model": "claude-...", "temperature": {"min":0,"max":1,"default":1}, ...} ]
        }

    Each entry flattens the model's ``param_ranges`` (temperature, top_p, …) to top level
    and adds key attributes. Public, read-only. Filters: ``?provider=openai``, ``?mode=chat``.
    (``temperature``/``top_p`` are populated for the curated common providers; other
    providers list the model + attributes without range data.)
    """
    try:
        catalog = await asyncio.to_thread(_build_model_catalog)
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for model_name, attrs in catalog.items():
            prov = attrs.get("litellm_provider")
            if not prov:
                continue
            if provider is not None and prov != provider:
                continue
            if mode is not None and attrs.get("mode") != mode:
                continue
            entry: Dict[str, Any] = {"model": model_name}
            # flatten param ranges (temperature, top_p, presence_penalty, ...) to top level
            for param_name, bounds in (attrs.get("param_ranges") or {}).items():
                entry[param_name] = bounds
            for attr in _MODELS_BY_PROVIDER_ATTRS:
                if attr in attrs and attrs[attr] is not None:
                    entry[attr] = attrs[attr]
            entry["supported_openai_params"] = attrs.get("supported_openai_params", [])
            grouped.setdefault(prov, []).append(entry)
        return grouped
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal Server Error ({str(e)})")


@router.get(
    "/public/litellm_blog_posts",
    tags=["public"],
    response_model=BlogPostsResponse,
)
async def get_litellm_blog_posts():
    """
    Public endpoint to get the latest LiteLLM blog posts.

    Fetches from GitHub with a 1-hour in-process cache.
    Falls back to the bundled local backup on any failure.
    """
    try:
        posts_data = get_blog_posts(url=litellm.blog_posts_url)
    except Exception as e:
        verbose_logger.warning("LiteLLM: get_litellm_blog_posts endpoint fallback triggered: %s", str(e))
        posts_data = GetBlogPosts.load_local_blog_posts()

    posts = [BlogPost(**p) for p in posts_data[:5]]
    return BlogPostsResponse(posts=posts)


@router.get(
    "/public/endpoints",
    tags=["public"],
    response_model=SupportedEndpointsResponse,
)
async def get_supported_endpoints() -> SupportedEndpointsResponse:
    """
    Return the list of LiteLLM proxy endpoints and which providers support each one.

    Reads from the bundled local backup file. Result is cached in-process for
    the lifetime of the server process.
    """
    global _cached_endpoints
    if _cached_endpoints is None:
        _cached_endpoints = SupportedEndpointsResponse(endpoints=_load_endpoints())  # type: ignore[arg-type]
    return _cached_endpoints


@router.get(
    "/public/agents/fields",
    tags=["public", "[beta] Agents"],
    response_model=List[AgentCreateInfo],
)
async def get_agent_fields() -> List[AgentCreateInfo]:
    """
    Return agent type metadata required by the dashboard create-agent flow.

    If an agent has `inherit_credentials_from_provider`, the provider's credential
    fields are automatically appended to the agent's credential_fields.
    """
    base_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "proxy",
        "public_endpoints",
    )

    agent_create_fields_path = os.path.join(base_path, "agent_create_fields.json")
    provider_create_fields_path = os.path.join(base_path, "provider_create_fields.json")

    with open(agent_create_fields_path, "r") as f:
        agent_create_fields = json.load(f)

    with open(provider_create_fields_path, "r") as f:
        provider_create_fields = json.load(f)

    # Build a lookup map for providers by name
    provider_map = {p["provider"]: p for p in provider_create_fields}

    # Merge inherited credential fields
    for agent in agent_create_fields:
        inherit_from = agent.get("inherit_credentials_from_provider")
        if inherit_from and inherit_from in provider_map:
            provider = provider_map[inherit_from]
            # Copy provider fields and mark them for inclusion in litellm_params
            inherited_fields = []
            for field in provider.get("credential_fields", []):
                field_copy = field.copy()
                field_copy["include_in_litellm_params"] = True
                inherited_fields.append(field_copy)
            # Append provider credential fields after agent's own fields
            agent["credential_fields"] = agent.get("credential_fields", []) + inherited_fields
        # Remove the inherit field from response (not needed by frontend)
        agent.pop("inherit_credentials_from_provider", None)

    return agent_create_fields
