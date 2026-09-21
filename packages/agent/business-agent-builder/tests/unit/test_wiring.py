"""Covers the feature/use-case dependency-injection loader used at bootstrap."""

import importlib

import pytest

import app.layer2_application.features.main_agent.use_cases as uc
from app.layer2_application.features.main_agent.use_cases.draft_business_agent_usecase import (
    DraftBusinessAgentUseCase,
)


class _Logger:
    def info(self, *_a, **_k):
        pass

    def warning(self, *_a, **_k):
        pass

    def error(self, *_a, **_k):
        pass


def _fresh_loader():
    # Other tests permanently stub uc.load_features/get_usecase; reload restores the real ones.
    module = importlib.reload(uc)
    module.FEATURE_REGISTRY.clear()
    module.USECASE_CONTAINER.clear()
    return module


def test_load_features_registers_draft_use_case_by_convention():
    loader = _fresh_loader()
    registry = loader.load_features(logger=_Logger())
    assert "draft_business_agent" in registry
    assert registry["draft_business_agent"] is DraftBusinessAgentUseCase


def test_load_all_usecases_autowires_by_param_name():
    loader = _fresh_loader()
    loader.load_features(logger=_Logger())

    deps = {"logger": _Logger(), "unused_dependency": object()}
    container = loader.load_all_usecases(deps, deps["logger"])

    use_case = container["draft_business_agent"]
    assert isinstance(use_case, DraftBusinessAgentUseCase)
    # openai_service not in deps -> defaulted to None (heuristic mode)
    assert use_case._llm is None
    assert loader.get_usecase("draft_business_agent") is use_case


def test_build_dependencies_skips_llm_when_api_key_missing(monkeypatch):
    # CF sets LLM_MODEL but may not provide OPENAI_API_KEY; OpenAIService() raises on an
    # empty key, so build_dependencies must skip it (heuristic mode) rather than crash boot.
    from app.layer4_frameworks.dependency import ioc_container as ioc

    monkeypatch.setattr(ioc.settings, "LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(ioc.settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(ioc.settings, "HANA_HOST", "")

    deps = ioc.build_dependencies()  # must not raise
    assert "openai_service" not in deps


def test_get_usecase_raises_for_unknown_name():
    loader = _fresh_loader()
    loader.load_features(logger=_Logger())
    loader.load_all_usecases({"logger": _Logger()}, _Logger())
    with pytest.raises(KeyError):
        loader.get_usecase("does_not_exist")
