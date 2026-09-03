"""Unit specs for two layer1 domain types with unexercised branches.

Targets:
  - worker_sdk/layer1_domain/entities/worker_function.py — WorkerFunction.to_definition()
    (the metadata projection used by FunctionRegistry.list_definitions).
  - worker_sdk/layer1_domain/value_objects/worker_capability.py (was 80%) —
    to_dict() / from_dict() round-trip were unexercised.

Pure domain: no collaborators, so no mocks. Expectations grounded in the source
(defaults come from ``field(default_factory=list)`` and ``str = ""``; from_dict
uses ``data.get(key, default)``).
"""

from worker_sdk.layer1_domain.entities.worker_function import (
    WorkerFunction,
    WorkerFunctionDefinition,
)
from worker_sdk.layer1_domain.value_objects.worker_capability import WorkerCapability


# --- WorkerFunction.to_definition ------------------------------------------

def test_to_definition_copies_metadata_and_drops_handler():
    # happy: to_definition projects name/description/schemas, no handler field.
    async def _h(params):  # pragma: no cover - never invoked here
        return {}

    fn = WorkerFunction(
        name="transform",
        description="does a thing",
        input_schema=[{"name": "in"}],
        output_schema=[{"name": "out"}],
        handler=_h,
    )
    definition = fn.to_definition()
    assert isinstance(definition, WorkerFunctionDefinition)
    assert definition.name == "transform"
    assert definition.description == "does a thing"
    assert definition.input_schema == [{"name": "in"}]
    assert definition.output_schema == [{"name": "out"}]
    assert not hasattr(definition, "handler")


def test_to_definition_defaults_when_minimal():
    # edge / boundary: only the required name given -> empty description + lists.
    definition = WorkerFunction(name="bare").to_definition()
    assert definition.name == "bare"
    assert definition.description == ""
    assert definition.input_schema == []
    assert definition.output_schema == []


def test_worker_function_default_handler_is_none():
    # source: handler defaults to None (the branch FunctionRegistry.invoke guards).
    assert WorkerFunction(name="x").handler is None


def test_worker_function_schema_defaults_are_independent_instances():
    # boundary: default_factory=list must not share one list across instances.
    a = WorkerFunction(name="a")
    b = WorkerFunction(name="b")
    a.input_schema.append({"k": 1})
    assert b.input_schema == []


# --- WorkerCapability round-trip -------------------------------------------

def test_capability_to_dict():
    # happy: to_dict emits exactly {domain, action}.
    cap = WorkerCapability(domain="mdg", action="research")
    assert cap.to_dict() == {"domain": "mdg", "action": "research"}


def test_capability_from_dict_full():
    # happy: from_dict reads both keys.
    cap = WorkerCapability.from_dict({"domain": "mdg", "action": "research"})
    assert cap.domain == "mdg"
    assert cap.action == "research"


def test_capability_from_dict_missing_keys_use_defaults():
    # invalid input / edge: absent keys fall back to "" (data.get(k, "")).
    cap = WorkerCapability.from_dict({})
    assert cap.domain == ""
    assert cap.action == ""


def test_capability_from_dict_partial():
    # boundary: only one key present; the other defaults.
    cap = WorkerCapability.from_dict({"domain": "d"})
    assert cap.domain == "d"
    assert cap.action == ""


def test_capability_round_trip():
    # from_dict(to_dict(x)) == x for a fully populated capability.
    cap = WorkerCapability(domain="d", action="a")
    assert WorkerCapability.from_dict(cap.to_dict()) == cap


def test_capability_defaults_empty():
    # boundary: default construction -> empty strings.
    cap = WorkerCapability()
    assert cap.to_dict() == {"domain": "", "action": ""}
