"""Unit spec — layer2_application LLMCostCalculator (pure service).

Gap-fill sweep 2026-07-02 (unit-smith). Baseline coverage of
``layer2_application/services/llm_cost_calculator.py`` was 84% (only the
happy dict-from_json + provider:model lookup was exercised indirectly by
test_llm_usage_tracking). Missing: invalid price-table entries, JSON parse
failure, non-mapping parsed JSON, disabled path, negative-token clamping,
model-only fallback, empty-table default. Cases grounded in source:
``agent_sdk/layer2_application/services/llm_cost_calculator.py``.

Five case types: happy · edge · invalid input · boundary · failure path.
"""

from agent_sdk.layer2_application.services.llm_cost_calculator import (
    LLMCostCalculator,
)


# ── happy path ─────────────────────────────────────────────────────────────
def test_calculate_uses_provider_model_price():
    calc = LLMCostCalculator(
        price_table={"openai:gpt-4o-mini": {"input_per_1m": 0.15, "output_per_1m": 0.60}},
    )
    result = calc.calculate(
        provider="openai",
        model="gpt-4o-mini",
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
    )
    assert result["input_cost"] == 0.15
    assert result["output_cost"] == 0.60
    assert result["total_cost"] == 0.75
    assert result["currency"] == "USD"


def test_from_json_parses_string_table():
    calc = LLMCostCalculator.from_json(
        '{"openai:gpt-4o-mini": {"input_per_1m": 0.15, "output_per_1m": 0.60}}',
        currency="EUR",
    )
    result = calc.calculate(
        provider="openai", model="gpt-4o-mini", prompt_tokens=2_000_000, completion_tokens=0
    )
    assert result["input_cost"] == 0.30
    assert result["currency"] == "EUR"


def test_from_json_accepts_mapping_directly():
    # Source: isinstance(raw, Mapping) -> passthrough.
    calc = LLMCostCalculator.from_json({"gpt-4o": {"input_per_1m": 1.0}})
    result = calc.calculate(
        provider=None, model="gpt-4o", prompt_tokens=1_000_000, completion_tokens=0
    )
    assert result["input_cost"] == 1.0


# ── edge: model-only fallback + key normalization ──────────────────────────
def test_lookup_falls_back_to_model_only_key():
    # Source: candidates include bare model_key when provider:model misses.
    calc = LLMCostCalculator(price_table={"gpt-4o": {"input_per_1m": 2.0, "output_per_1m": 4.0}})
    result = calc.calculate(
        provider="unknownvendor",
        model="GPT-4O",  # normalized to lower
        prompt_tokens=1_000_000,
        completion_tokens=0,
    )
    assert result["input_cost"] == 2.0


def test_currency_blank_defaults_to_usd():
    # Source: self.currency = (currency or "USD").strip() or "USD".
    calc = LLMCostCalculator(currency="   ")
    assert calc.currency == "USD"


# ── invalid input ──────────────────────────────────────────────────────────
def test_non_mapping_price_entry_is_skipped():
    # Source: `if not isinstance(value, Mapping): continue`.
    calc = LLMCostCalculator(price_table={"gpt-4o": "not-a-mapping"})  # type: ignore[dict-item]
    result = calc.calculate(
        provider=None, model="gpt-4o", prompt_tokens=1_000_000, completion_tokens=0
    )
    # entry skipped -> no price -> zero cost
    assert result["total_cost"] == 0.0


def test_non_numeric_price_coerces_to_zero():
    # Source: _to_float catches TypeError/ValueError -> 0.0.
    calc = LLMCostCalculator(
        price_table={"gpt-4o": {"input_per_1m": "abc", "output_per_1m": None}}
    )
    result = calc.calculate(
        provider=None, model="gpt-4o", prompt_tokens=1_000_000, completion_tokens=1_000_000
    )
    assert result["input_cost"] == 0.0
    assert result["output_cost"] == 0.0


def test_from_json_invalid_json_yields_empty_table():
    # Source: json.JSONDecodeError -> parsed = {}.
    calc = LLMCostCalculator.from_json("{not valid json")
    result = calc.calculate(
        provider="openai", model="gpt-4o", prompt_tokens=1_000_000, completion_tokens=0
    )
    assert result["total_cost"] == 0.0


def test_from_json_non_mapping_json_yields_empty_table():
    # Source: `if not isinstance(parsed, Mapping): parsed = {}`.
    calc = LLMCostCalculator.from_json("[1, 2, 3]")
    result = calc.calculate(
        provider="openai", model="gpt-4o", prompt_tokens=1_000_000, completion_tokens=0
    )
    assert result["total_cost"] == 0.0


# ── boundary ───────────────────────────────────────────────────────────────
def test_disabled_calculator_returns_zeros():
    # Source: `if not self.enabled: return zeros`.
    calc = LLMCostCalculator(
        price_table={"gpt-4o": {"input_per_1m": 5.0, "output_per_1m": 5.0}},
        enabled=False,
    )
    result = calc.calculate(
        provider=None, model="gpt-4o", prompt_tokens=9_999, completion_tokens=9_999
    )
    assert result == {
        "input_cost": 0.0,
        "output_cost": 0.0,
        "total_cost": 0.0,
        "currency": "USD",
    }


def test_negative_tokens_are_clamped_to_zero():
    # Source: max(prompt_tokens, 0).
    calc = LLMCostCalculator(price_table={"gpt-4o": {"input_per_1m": 10.0, "output_per_1m": 10.0}})
    result = calc.calculate(
        provider=None, model="gpt-4o", prompt_tokens=-500, completion_tokens=-500
    )
    assert result["input_cost"] == 0.0
    assert result["output_cost"] == 0.0


def test_from_json_empty_string_and_none_yield_empty_table():
    # Source: `if not raw_price_table: return cls(price_table={}, ...)`.
    for raw in ("", None):
        calc = LLMCostCalculator.from_json(raw)
        result = calc.calculate(
            provider="openai", model="gpt-4o", prompt_tokens=1_000_000, completion_tokens=0
        )
        assert result["total_cost"] == 0.0


# ── failure path: unknown model -> zero-price default, not an exception ─────
def test_unknown_model_returns_zero_price_not_error():
    # Source: _lookup_price returns {input:0, output:0} default.
    calc = LLMCostCalculator(price_table={"gpt-4o": {"input_per_1m": 1.0}})
    result = calc.calculate(
        provider="acme", model="never-seen", prompt_tokens=1_000_000, completion_tokens=1_000_000
    )
    assert result["input_cost"] == 0.0
    assert result["output_cost"] == 0.0
    assert result["total_cost"] == 0.0
