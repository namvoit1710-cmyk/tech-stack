"""The guarded rule-expression evaluator.

Two properties are under test and they pull in opposite directions, which is
the point: an `expression` rule has to accept whatever a rule author actually
writes, and it must not be a way to run arbitrary Python.

The escape cases are not hypothetical. Both of them worked against the context
this module replaced (``{"pl": pl, "col": pl.col, "lit": pl.lit}``):
``pl.__loader__.__init__.__globals__['__builtins__']['open']`` walks back to
real builtins, and ``pl.os.getcwd()`` does the same in one hop, because
``polars/__init__.py`` does ``import os``.
"""

import polars as pl
import pytest

from app.layer4_frameworks.providers.expressions.safe_expression import (
    CALLABLE_ATTRS,
    IO_ATTRS,
    RuleExpressionError,
    compile_expression,
    evaluate_expression,
    is_safe,
)


@pytest.fixture
def frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "baseUnit": ["EA", "CASE", "P" * 41],
            "email": ["a@b.com", "nope", "c@d.org"],
            "total_order": [500, 2000, 1500],
            "customer_level": ["STD", "VIP", "STD"],
            "validFrom": ["2019-01-01", "2021-06-01", "2023-02-02"],
            "amount": [10.0, 20.0, 30.0],
            "qty": [1, 2, 3],
            "tag": ["x", "X", None],
        }
    )


def _run(frame: pl.DataFrame, expression, columns=None):
    value = evaluate_expression(expression, columns=columns, frame=frame)
    return frame.select(value.alias("_out")).to_series().to_list()


# ---------------------------------------------------------------- the catalog

def test_the_rules_already_in_the_catalog_still_evaluate(frame):
    """These three are real: one from ART41.00, two seeded in validation_rules_db."""
    assert _run(frame, "pl.col('baseUnit').cast(pl.Utf8).str.len_chars() <= 40") == [
        True, True, False,
    ]
    assert _run(
        frame,
        r"pl.col(cols[0]).str.contains("
        r"r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')",
        columns=["email"],
    ) == [True, False, True]
    assert _run(
        frame,
        "pl.when(pl.col(cols[0]).cast(pl.Float64, strict=False) > 1000)"
        ".then(pl.col(cols[1]) == 'VIP').otherwise(True)",
        columns=["total_order", "customer_level"],
    ) == [True, True, False]


# ---------------------------------------------------------------- the dialects

@pytest.mark.parametrize(
    "label,expression",
    [
        ("canonical", "pl.col('qty') > 1"),
        ("bare col", "col('qty') > 1"),
        ("pyspark habit", "F.col('qty') > 1"),
        ("module spelled out", "polars.col('qty') > 1"),
        ("short alias", "p.col('qty') > 1"),
        ("markdown fence", "```python\npl.col('qty') > 1\n```"),
        ("pasted import header", "import polars as pl\npl.col('qty') > 1"),
        ("from-import header", "from polars import col\ncol('qty') > 1"),
        ("trailing semicolon", "pl.col('qty') > 1;"),
        ("explicit python tag", "python: pl.col('qty') > 1"),
        ("frame subscript", "df['qty'] > 1"),
        ("lambda over the frame", "lambda df: df['qty'] > 1"),
        ("return statement", "return pl.col('qty') > 1"),
        ("bare assignment", "keep = pl.col('qty') > 1"),
        ("multi-line with a local", "cut = 1\npl.col('qty') > cut"),
        ("sql tag", "sql: qty > 1"),
        ("bare sql", "qty > 1"),
    ],
)
def test_every_accepted_dialect_means_the_same_thing(frame, label, expression):
    """One predicate, seventeen ways of writing it. All land on the same rows."""
    assert _run(frame, expression) == [False, True, True], label


def test_a_non_polars_import_is_refused_rather_than_quietly_stripped(frame):
    """Dropping every import would turn `import os` into a question about
    whether `os` happens to be a known name. It should not get that far."""
    with pytest.raises(RuleExpressionError, match="may not import 'os'"):
        evaluate_expression("import os\nos.getcwd()", frame=frame)


# ------------------------------------------------------------- expressiveness

def test_comprehensions_reach_across_the_column_list(frame):
    assert _run(
        frame,
        "pl.any_horizontal([pl.col(c).is_null() for c in cols])",
        columns=["tag", "qty"],
    ) == [False, False, True]


def test_f_strings_build_column_names(frame):
    assert _run(frame, "pl.col(f'{cols[0]}').is_not_null()", columns=["qty"]) == [
        True, True, True,
    ]


def test_the_date_namespace_survives_the_guard(frame):
    assert _run(
        frame, "pl.col('validFrom').str.to_date('%Y-%m-%d').dt.year() >= 2020"
    ) == [False, True, True]


def test_selectors_are_in_scope(frame):
    assert _run(frame, "pl.all_horizontal(cs.numeric().is_not_null())") == [
        True, True, True,
    ]


def test_a_bare_builtin_keeps_python_meaning_not_the_polars_one(frame):
    """`len(cols)` is Python's len over the column list; `pl.len()` is the
    polars row count. Both are reachable, and they do not collide."""
    assert _run(
        frame,
        "pl.lit(len(cols) == 2) & pl.col('qty').is_not_null()",
        columns=["a", "b"],
    ) == [True] * 3
    assert _run(frame, "pl.lit(sum([1, 2]) == 3) & (pl.col('qty') > 1)") == [
        False, True, True,
    ]
    # pl.len() is the polars row count, so it aggregates to a single row.
    assert _run(frame, "pl.len() == 3") == [True]


def test_a_multi_line_body_can_name_its_intermediate_steps(frame):
    expression = (
        "cheap = pl.col('amount') <= 20\n"
        "tagged = pl.col('tag').is_not_null()\n"
        "cheap & tagged"
    )
    assert _run(frame, expression) == [True, True, False]


def test_nested_when_chains_and_horizontal_aggregates(frame):
    assert _run(
        frame,
        "pl.when(pl.col('qty') > 2).then(pl.lit('big'))"
        ".when(pl.col('qty') > 1).then(pl.lit('mid')).otherwise(pl.lit('small'))",
    ) == ["small", "mid", "big"]
    assert _run(
        frame, "pl.all_horizontal(pl.col('qty') > 0, pl.col('amount') > 5)"
    ) == [True, True, True]


def test_a_series_or_a_literal_is_coerced_to_an_expression(frame):
    assert _run(frame, "df['qty'] > 1") == [False, True, True]
    assert _run(frame, "True") == [True]


def test_a_frame_result_is_refused_with_an_explanation(frame):
    with pytest.raises(RuleExpressionError, match="needs a single expression"):
        evaluate_expression("df.select('qty')", frame=frame)


# -------------------------------------------------------------- the refusals

@pytest.mark.parametrize(
    "label,expression",
    [
        ("object subclass walk", "().__class__.__bases__[0].__subclasses__()"),
        ("module globals walk", "pl.__loader__.__init__.__globals__"),
        ("reach open", "pl.__loader__.__init__.__globals__['__builtins__']['open']"),
        ("dunder on an expression", "pl.col('qty').__class__"),
        ("dunder inside a lambda", "lambda df: df.__class__"),
        ("dunder inside a comprehension", "[c.__class__ for c in cols]"),
        ("dunder via a local alias", "t = pl\nt.__loader__"),
        ("import through builtins", "__import__('os')"),
        ("an unbound name", "os.system('echo hi')"),
    ],
)
def test_the_escape_routes_are_refused(frame, label, expression):
    with pytest.raises(RuleExpressionError):
        evaluate_expression(expression, columns=["qty"], frame=frame)


@pytest.mark.parametrize(
    "expression",
    [
        "pl.col('qty').map_elements(lambda v: v)",
        "pl.col('qty').map_batches(lambda s: s)",
        "pl.col('qty').pipe(print)",
    ],
)
def test_anything_taking_an_arbitrary_callable_is_refused(frame, expression):
    with pytest.raises(RuleExpressionError, match="arbitrary Python"):
        evaluate_expression(expression, frame=frame)


@pytest.mark.parametrize(
    "expression",
    [
        "pl.read_csv('/etc/passwd')",
        "pl.scan_csv('C:/Windows/win.ini')",
        "df.write_csv('out.csv')",
        "pl.SQLContext()",
        "pl.Config.load_from_file('x')",
    ],
)
def test_file_and_sql_access_is_refused(frame, expression):
    with pytest.raises(RuleExpressionError, match="may not touch files"):
        evaluate_expression(expression, frame=frame)


@pytest.mark.parametrize(
    "label,expression",
    [
        ("pl.os leak", "pl.os.getcwd()"),
        ("aliased leak", "F.os.getcwd()"),
        ("submodule leak", "pl.io.csv"),
    ],
)
def test_modules_polars_imports_are_not_reachable(frame, label, expression):
    """`polars/__init__.py` does `import os`, so a raw module in scope hands out
    `pl.os.system` with no dunder walking at all."""
    with pytest.raises(RuleExpressionError, match="not a polars export"):
        evaluate_expression(expression, frame=frame)


def test_a_rule_may_not_mutate_the_shared_polars_namespace(frame):
    with pytest.raises(RuleExpressionError, match="may not assign"):
        evaluate_expression("pl.col = 1", frame=frame)


def test_the_module_leak_is_caught_at_compile_time_not_run_time():
    """The boundary check calls compile only. If `pl.os` were merely an
    AttributeError at evaluation, an unsafe rule would pass validation."""
    assert is_safe("pl.col('a') > 1") is True
    assert is_safe("pl.os.getcwd()") is False
    with pytest.raises(RuleExpressionError):
        compile_expression("pl.os.getcwd()")


# ------------------------------------------------------- cost of the guard

def test_the_guard_removes_only_callables_and_io():
    """If this number drops, the guard has started costing expressiveness."""
    surface = {m for m in dir(pl.col("x")) if not m.startswith("_")}
    for namespace in ("str", "dt", "list", "arr", "struct", "cat", "bin", "name"):
        surface |= {
            m for m in dir(getattr(pl.col("x"), namespace)) if not m.startswith("_")
        }
    removed = surface & (CALLABLE_ATTRS | IO_ATTRS)
    assert removed <= {
        "apply", "map", "map_alias", "map_batches", "map_elements", "pipe",
        "rolling_map", "register_plugin", "deserialize", "serialize",
    }
    assert len(surface - removed) / len(surface) > 0.95


# --------------------------------------------------------------- input shapes

@pytest.mark.parametrize("value", [None, 42, [], {}, object()])
def test_a_non_string_expression_is_a_clean_error(value):
    with pytest.raises(RuleExpressionError, match="must be a string"):
        evaluate_expression(value)


@pytest.mark.parametrize("value", ["", "   ", "\n\n", "```\n```", "python:"])
def test_an_empty_expression_is_a_clean_error(value):
    with pytest.raises(RuleExpressionError, match="empty"):
        evaluate_expression(value)


def test_a_syntax_error_names_the_line():
    with pytest.raises(RuleExpressionError, match="not a valid expression"):
        evaluate_expression("pl.col('a' >")


def test_a_missing_column_is_polars_business_not_the_guards(frame):
    """The guard compiles the expression; it does not resolve columns. A
    reference to a column that is not there is valid polars and fails when the
    frame is evaluated, which is where the message belongs."""
    assert is_safe("pl.col('no_such_column').str.len_chars() > 0") is True
    with pytest.raises(pl.exceptions.ColumnNotFoundError):
        _run(frame, "pl.col('no_such_column').str.len_chars() > 0")


def test_an_expression_that_cannot_run_at_all_is_a_rule_error(frame):
    with pytest.raises(RuleExpressionError, match="failed to evaluate"):
        evaluate_expression("pl.col('qty', 'amount', bad_kwarg=1)", frame=frame)
