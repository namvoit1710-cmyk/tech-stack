import dataclasses


def test_agent_registration_exposes_typed_registry_contract_fields():
    from agent_sdk.layer1_domain.entities.agent_registration import AgentRegistration

    field_names = {field.name for field in dataclasses.fields(AgentRegistration)}

    assert "kind" in field_names
    assert "is_published" in field_names
    assert "agent_runtime_config" in field_names
    assert "execution_policy" in field_names
    assert "attached_agent_ids" in field_names
    assert "queue_metadata" in field_names


def test_agent_registration_supports_lightweight_defaults():
    from agent_sdk.layer1_domain.entities.agent_registration import AgentRegistration

    registration = AgentRegistration(
        agent_type="simple-agent",
        version="1.0.0",
        sdk_version="1.0.0",
        domain="general",
        endpoint_url="http://agent:8000",
    )

    assert registration.kind == "SERVICE"
    assert registration.is_published is True
    assert registration.agent_runtime_config is None
    assert registration.execution_policy is None
    assert registration.attached_agent_ids == []
    assert registration.queue_metadata is None
    assert registration.metadata == {}


def test_runtime_and_queue_contract_models_are_typed_dataclasses():
    from agent_sdk.layer1_domain.entities.agent_runtime_config import AgentRuntimeConfig
    from agent_sdk.layer1_domain.entities.execution_policy import ExecutionPolicy
    from agent_sdk.layer1_domain.entities.queue_metadata import QueueMetadata

    runtime_fields = {field.name for field in dataclasses.fields(AgentRuntimeConfig)}
    policy_fields = {field.name for field in dataclasses.fields(ExecutionPolicy)}
    queue_fields = {field.name for field in dataclasses.fields(QueueMetadata)}

    assert runtime_fields >= {
        "system_prompt",
        "max_concurrency",
        "llm_model",
        "llm_provider",
        "llm_temperature",
    }
    assert policy_fields >= {"supports_streaming", "supports_human_in_the_loop"}
    assert queue_fields >= {
        "queue_name",
        "request_topic",
        "reply_topic",
        "delivery_hints",
    }


def test_agent_capability_supports_semantic_intents_and_queue_metadata():
    from agent_sdk.layer1_domain.entities.agent_capability import AgentCapability

    field_names = {field.name for field in dataclasses.fields(AgentCapability)}

    assert "semantic_intents" in field_names
    assert "queue_metadata" in field_names
