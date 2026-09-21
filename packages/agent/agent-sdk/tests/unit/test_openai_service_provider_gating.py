"""Unit spec — LLM seam provider gating: looks_like_openai + supports_strict_json_schema.

Gap-fill sweep 2026-07-02 (unit-smith). openai_service.py sat at 71%. These
two pure gating helpers (which decide when OpenAI-only behaviours like strict
JSON schema / reasoning_effort apply — canon invariant #4) had NO dedicated
test. Cases grounded in source:
``agent_sdk/layer4_frameworks/ai/openai_service.py``
(``looks_like_openai``, ``supports_strict_json_schema``,
``_OPENAI_STRICT_JSON_SCHEMA_MODEL_PREFIXES``).

Five case types: happy · edge · invalid input · boundary · failure path.
"""

import pytest

from agent_sdk.layer4_frameworks.ai.openai_service import (
    looks_like_openai,
    supports_strict_json_schema,
)


# ══ looks_like_openai ═══════════════════════════════════════════════════════
# ── happy path: explicit provider ──
@pytest.mark.parametrize("provider", ["openai", "azure_openai", "azure-openai", "OpenAI"])
def test_looks_like_openai_by_provider(provider):
    # Source: provider_key in {"openai","azure_openai"} after hyphen->underscore, lower.
    assert looks_like_openai(provider, "anything") is True


# ── edge: inferred from model prefix when provider is generic ──
@pytest.mark.parametrize("model", ["gpt-4o-mini", "o1-preview", "o3-mini", "o4-x", "GPT-4.1"])
def test_looks_like_openai_by_model_prefix(model):
    # Source: model_key.startswith(("gpt-","o1","o3","o4")); model lowercased.
    assert looks_like_openai(None, model) is True


# ── invalid input / boundary: None + non-openai ──
def test_looks_like_openai_false_for_non_openai():
    assert looks_like_openai("anthropic", "claude-3-5-sonnet-latest") is False
    assert looks_like_openai(None, None) is False
    assert looks_like_openai("", "") is False


def test_looks_like_openai_ollama_provider_is_false():
    assert looks_like_openai("ollama", "llama3") is False


# ══ supports_strict_json_schema ════════════════════════════════════════════
# ── happy path: OpenAI + strict-capable prefix ──
@pytest.mark.parametrize("model", ["gpt-4o", "gpt-4.1", "gpt-4.5", "gpt-5", "o1", "o3", "o4"])
def test_supports_strict_schema_for_capable_openai_models(model):
    assert supports_strict_json_schema("openai", model) is True


# ── edge: OpenAI-looking but not a strict-capable prefix ──
def test_strict_schema_false_for_older_openai_model():
    # Source: looks_like_openai True (gpt- prefix) but "gpt-3.5" not in strict prefixes.
    assert supports_strict_json_schema("openai", "gpt-3.5-turbo") is False


# ── failure path: non-openai provider AND non-openai model -> no strict schema ──
def test_strict_schema_false_for_non_openai_provider_and_model():
    # Source: `if not looks_like_openai(...): return False` guard. Note the model
    # must ALSO not match an OpenAI prefix — looks_like_openai ORs provider with
    # model prefix, so ("anthropic","gpt-4o") is still openai-looking via the model.
    assert supports_strict_json_schema("anthropic", "claude-3-5-sonnet-latest") is False


def test_strict_schema_true_when_model_prefix_looks_openai_despite_provider():
    # Documented behaviour: gpt-4o model matches an OpenAI prefix, so even with a
    # non-openai provider label looks_like_openai() is True and strict schema applies.
    assert supports_strict_json_schema("anthropic", "gpt-4o") is True


# ── boundary: None inputs ──
def test_strict_schema_false_for_none_inputs():
    assert supports_strict_json_schema(None, None) is False
