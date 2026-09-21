import pytest

from app.layer1_domain.entities.business_agent_draft import BusinessAgentDraft


def _valid(**overrides):
    base = dict(
        name="Support Bot",
        description="Answers customer questions.",
        business="You are a support agent.",
    )
    base.update(overrides)
    return BusinessAgentDraft(**base)


def test_valid_draft_defaults_to_business_active_custom():
    draft = _valid()
    assert draft.kind == "business"
    assert draft.status == "active"
    assert draft.config_type == "custom"
    assert draft.is_published is False
    assert draft.version == "1.0.0"


def test_strips_whitespace_on_text_fields():
    draft = _valid(name="  Support Bot  ", description="  desc  ", business="  ctx  ")
    assert draft.name == "Support Bot"
    assert draft.description == "desc"
    assert draft.business == "ctx"


@pytest.mark.parametrize("field", ["name", "description", "business"])
def test_requires_non_empty_core_fields(field):
    with pytest.raises(ValueError):
        _valid(**{field: "   "})


def test_rejects_overlong_name():
    with pytest.raises(ValueError):
        _valid(name="x" * 256)


def test_rejects_invalid_kind_status_config_type():
    with pytest.raises(ValueError):
        _valid(kind="robot")
    with pytest.raises(ValueError):
        _valid(status="paused")
    with pytest.raises(ValueError):
        _valid(config_type="weird")


def test_rejects_out_of_range_temperature():
    with pytest.raises(ValueError):
        _valid(temperature=1.5)


def test_normalizes_case_for_enumish_fields():
    draft = _valid(kind="BUSINESS", status="Active", config_type="Default")
    assert (draft.kind, draft.status, draft.config_type) == (
        "business",
        "active",
        "default",
    )


def test_to_register_payload_matches_register_request_shape():
    draft = _valid(
        capabilities=["answer_faq"],
        tools=["kb_search"],
        workflows=["wf-1"],
        model="gpt-4o-mini",
        provider="openai",
        temperature=0.3,
    )
    payload = draft.to_register_payload()

    # Required RegisterAgentRequest keys are present.
    for key in ("name", "description", "kind", "status", "config_type", "business"):
        assert key in payload

    assert payload["tools"] == ["kb_search"]
    assert payload["workflows"] == ["wf-1"]
    assert payload["model"] == "gpt-4o-mini"
    assert payload["provider"] == "openai"
    assert payload["temperature"] == 0.3
    assert payload["metadata"] == {"capabilities": ["answer_faq"]}


def test_to_register_payload_omits_optional_llm_fields_when_unset():
    payload = _valid().to_register_payload()
    assert "model" not in payload
    assert "provider" not in payload
    assert "temperature" not in payload
    assert "metadata" not in payload


def test_from_mapping_builds_draft_and_ignores_unknown_keys():
    draft = BusinessAgentDraft.from_mapping(
        {
            "name": "KB Bot",
            "description": "Knows things.",
            "business": "You answer questions.",
            "capabilities": ["answer_faq"],
            "unknown_key": "ignored",
        }
    )
    assert isinstance(draft, BusinessAgentDraft)
    assert draft.name == "KB Bot"
    assert draft.capabilities == ["answer_faq"]


def test_from_mapping_returns_same_instance_when_already_a_draft():
    draft = _valid()
    assert BusinessAgentDraft.from_mapping(draft) is draft
