"""Unit specs for the pure layer1 domain service QueryEvaluator.

Target: app/layer1_domain/services/query_evaluator.py

These are the 3 evaluate branches lifted verbatim from the old
ProfileManagerProcessor.evaluate_query_result — same verdicts, new shape.
"""
from app.layer1_domain.entities.query_result import QueryResult
from app.layer1_domain.services.query_evaluator import QueryEvaluator


evaluator = QueryEvaluator()


# failure path: API error -> INCORRECT, message echoes the error
def test_evaluate_error_branch():
    out = evaluator.evaluate(
        QueryResult(users=[], odata_query="q", user_prompt="p", error="bad rule")
    )
    assert out.valid == "incorrect"
    assert out.message == "This rule is not supported: bad rule"
    assert out.item_size == "0"
    assert out.expression == "q"
    assert out.user_prompt == "p"


# boundary: zero items (no error) -> WARNING
def test_evaluate_empty_items_warning():
    out = evaluator.evaluate(QueryResult(users=[], odata_query="q", user_prompt="p"))
    assert out.valid == "warning"
    assert out.message == "Rule is valid but no matching. Are you sure?"
    assert out.item_size == "0"


# happy: has items -> CORRECT, item_size reflects count
def test_evaluate_correct_branch():
    out = evaluator.evaluate(
        QueryResult(users=[{"id": 1}, {"id": 2}], odata_query="q", user_prompt="p")
    )
    assert out.valid == "correct"
    assert out.message == ""
    assert out.item_size == "2"


# boundary: empty QueryResult -> defaults kick in -> WARNING (matches old {} case)
def test_evaluate_empty_result_defaults_to_warning():
    out = evaluator.evaluate(QueryResult())
    assert out.valid == "warning"
    assert out.item_size == "0"
    assert out.expression == ""
    assert out.user_prompt == ""
