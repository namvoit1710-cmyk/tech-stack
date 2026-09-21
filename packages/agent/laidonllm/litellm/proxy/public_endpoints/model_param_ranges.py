"""Curated sampling-parameter ranges by provider (+ per-model overrides).

Temperature / top_p / etc. valid ranges are provider-level conventions, not
per-model, so we key by ``litellm_provider`` and keep a small set of explicit
model overrides (e.g. OpenAI o-series reasoning models don't accept a variable
temperature). Values are curated/approximate and safe to tune — they exist so a
downstream service can validate or clamp params *before* calling the provider,
instead of round-tripping for a 400.

Consumed by the /public/model_param_ranges and /public/model_catalog endpoints.
"""
from typing import Any, Dict

# provider -> { param -> {min, max, default?} }
PROVIDER_PARAM_RANGES: Dict[str, Dict[str, Dict[str, Any]]] = {
    "openai": {
        "temperature": {"min": 0.0, "max": 2.0, "default": 1.0},
        "top_p": {"min": 0.0, "max": 1.0, "default": 1.0},
        "presence_penalty": {"min": -2.0, "max": 2.0, "default": 0.0},
        "frequency_penalty": {"min": -2.0, "max": 2.0, "default": 0.0},
    },
    "azure": {
        "temperature": {"min": 0.0, "max": 2.0, "default": 1.0},
        "top_p": {"min": 0.0, "max": 1.0, "default": 1.0},
        "presence_penalty": {"min": -2.0, "max": 2.0, "default": 0.0},
        "frequency_penalty": {"min": -2.0, "max": 2.0, "default": 0.0},
    },
    "anthropic": {
        "temperature": {"min": 0.0, "max": 1.0, "default": 1.0},
        "top_p": {"min": 0.0, "max": 1.0},
        "top_k": {"min": 0},
    },
    "bedrock": {
        "temperature": {"min": 0.0, "max": 1.0, "default": 0.5},
        "top_p": {"min": 0.0, "max": 1.0},
        "top_k": {"min": 0},
    },
    "vertex_ai": {
        "temperature": {"min": 0.0, "max": 2.0, "default": 1.0},
        "top_p": {"min": 0.0, "max": 1.0},
        "top_k": {"min": 1},
    },
    "gemini": {
        "temperature": {"min": 0.0, "max": 2.0, "default": 1.0},
        "top_p": {"min": 0.0, "max": 1.0},
        "top_k": {"min": 1},
    },
    "cohere": {
        "temperature": {"min": 0.0, "max": 1.0, "default": 0.3},
        "top_p": {"min": 0.0, "max": 1.0},
        "top_k": {"min": 0},
    },
    "mistral": {
        "temperature": {"min": 0.0, "max": 1.0, "default": 0.7},
        "top_p": {"min": 0.0, "max": 1.0},
    },
    "groq": {
        "temperature": {"min": 0.0, "max": 2.0, "default": 1.0},
        "top_p": {"min": 0.0, "max": 1.0},
    },
}

# OpenAI-compatible providers share OpenAI's ranges.
PROVIDER_PARAM_RANGES["azure_ai"] = PROVIDER_PARAM_RANGES["openai"]
PROVIDER_PARAM_RANGES["text-completion-openai"] = PROVIDER_PARAM_RANGES["openai"]

# Exact-or-prefix model overrides. OpenAI o-series reasoning models don't accept a
# variable temperature (fixed at 1 / ignored), so mark it unsupported.
MODEL_PARAM_OVERRIDES: Dict[str, Dict[str, Dict[str, Any]]] = {
    "o1": {"temperature": {"supported": False}},
    "o1-mini": {"temperature": {"supported": False}},
    "o1-preview": {"temperature": {"supported": False}},
    "o1-pro": {"temperature": {"supported": False}},
    "o3": {"temperature": {"supported": False}},
    "o3-mini": {"temperature": {"supported": False}},
    "o3-pro": {"temperature": {"supported": False}},
    "o4-mini": {"temperature": {"supported": False}},
}


def _strip_provider_prefix(model: str) -> str:
    # "openai/gpt-4o" -> "gpt-4o"; "azure/o1" -> "o1"
    return model.split("/", 1)[1] if "/" in model else model


def get_param_ranges_for_model(model: str, provider: str) -> Dict[str, Dict[str, Any]]:
    """Provider defaults merged with any per-model override (exact then prefix match)."""
    base = PROVIDER_PARAM_RANGES.get(provider or "", {})
    ranges: Dict[str, Dict[str, Any]] = {param: dict(bounds) for param, bounds in base.items()}

    bare = _strip_provider_prefix(model or "")
    override = MODEL_PARAM_OVERRIDES.get(bare)
    if override is None:
        for key, val in MODEL_PARAM_OVERRIDES.items():
            if bare.startswith(key):
                override = val
                break
    if override:
        for param, bounds in override.items():
            merged = dict(ranges.get(param, {}))
            merged.update(bounds)
            ranges[param] = merged
    return ranges
