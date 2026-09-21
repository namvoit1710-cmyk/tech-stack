"""Unit tests for the operation registry and operation metadata.

Dependency-free: operation modules are pure dicts and operation_registry uses
only the stdlib, so these run without the service's runtime deps (polars,
fastapi, langgraph, ...). Runnable via pytest or standalone `python`.
"""
import importlib
import os
import pkgutil
import sys

# Make the service root importable (so `import app...` works) whether run via
# pytest from the repo or standalone.
_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

import app.layer1_domain.operations as _operations_pkg  # noqa: E402


def load_operations():
    """Discover operation definitions the same way the planner does
    (pkgutil over the operations package) — dependency-free, and independent of
    any operation_registry module that may only exist on other branches."""
    ops = []
    for _, module_name, _ in pkgutil.iter_modules(_operations_pkg.__path__):
        module = importlib.import_module(f"{_operations_pkg.__name__}.{module_name}")
        if hasattr(module, "OPERATION"):
            ops.append(module.OPERATION)
    return ops


REQUIRED_KEYS = {"scope", "type", "description", "example"}


def _ops_by_type():
    return {op["type"]: op for op in load_operations()}


def test_all_operations_have_required_shape():
    ops = load_operations()
    assert ops, "operation registry loaded no operations"
    for op in ops:
        missing = REQUIRED_KEYS - set(op)
        assert not missing, f"operation {op.get('type')!r} missing keys: {missing}"
        assert op["scope"] == "column"
        ex = op["example"]
        assert ex.get("type") == op["type"], f"example.type mismatch for {op['type']!r}"
        assert "params" in ex and "columns" in ex["params"]


def test_reference_lookup_operation_is_registered():
    ops = _ops_by_type()
    assert "reference_lookup" in ops, (
        "reference_lookup operation not discovered by the registry — "
        "the agent/planner cannot emit dangerous-substance checks"
    )


def test_reference_lookup_params_match_datafactory_handler():
    op = _ops_by_type()["reference_lookup"]
    params = op["example"]["params"]
    # Must carry a reference pointer the data-factory reference_lookup rule understands.
    assert params.get("reference_id") or params.get("reference_source"), (
        "reference_lookup example must provide reference_id or reference_source"
    )
    assert params.get("violate_when") in {"in_set", "not_in_set"}
    assert isinstance(params.get("columns"), list) and params["columns"]
    # Description should steer the planner toward the dangerous-substance use case.
    desc = op["description"].lower()
    assert "dangerous" in desc and "reference" in desc


def test_planner_prompt_includes_dangerous_reference_lookup_guidance():
    """The planner system prompt must surface the reference_lookup operation AND the
    explicit natural-language mapping for 'no dangerous things allowed' -> reference_lookup,
    otherwise the LLM planner won't emit it for SA-1358 Usecase 1."""
    from app.layer2_application.features.execute_agent.operation_loader import build_operations_prompt
    from app.layer2_application.features.execute_agent.prompt.transformation_planner_prompt import build_plan_prompt

    operations_prompt = build_operations_prompt(load_operations())
    system_prompt = build_plan_prompt({"header": {"header_columns": ["Material"]}}, operations_prompt)

    # operation is injected
    assert "reference_lookup" in system_prompt
    assert "dangerously_validate" in system_prompt
    # explicit planning-rule + example steer the mapping
    assert "no dangerous things" in system_prompt.lower()
    assert "violate_when" in system_prompt


if __name__ == "__main__":
    test_all_operations_have_required_shape()
    test_reference_lookup_operation_is_registered()
    test_reference_lookup_params_match_datafactory_handler()
    test_planner_prompt_includes_dangerous_reference_lookup_guidance()
    print("OK: all operation tests passed")
    print("registered operation types:", sorted(_ops_by_type().keys()))
