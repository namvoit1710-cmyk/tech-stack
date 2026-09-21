from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast


class LLMCostCalculator:
    def __init__(
        self,
        *,
        price_table: Mapping[str, Mapping[str, Any]] | None = None,
        currency: str = "USD",
        enabled: bool = True,
    ) -> None:
        self._price_table: dict[str, dict[str, float]] = {}
        for key, value in (price_table or {}).items():
            if not isinstance(value, Mapping):
                continue
            normalized_key = self._normalize_key_string(str(key))
            self._price_table[normalized_key] = {
                "input_per_1m": self._to_float(value.get("input_per_1m")),
                "output_per_1m": self._to_float(value.get("output_per_1m")),
            }
        self.currency = (currency or "USD").strip() or "USD"
        self.enabled = enabled

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _normalize_key_string(key: str) -> str:
        return key.strip().lower()

    @classmethod
    def from_json(
        cls,
        raw_price_table: str | Mapping[str, Mapping[str, Any]] | None,
        *,
        currency: str = "USD",
        enabled: bool = True,
    ) -> "LLMCostCalculator":
        if isinstance(raw_price_table, Mapping):
            return cls(
                price_table=cast(Mapping[str, Mapping[str, Any]], raw_price_table),
                currency=currency,
                enabled=enabled,
            )
        if not raw_price_table:
            return cls(price_table={}, currency=currency, enabled=enabled)
        try:
            parsed = json.loads(raw_price_table)
        except (json.JSONDecodeError, TypeError):
            parsed = {}
        if not isinstance(parsed, Mapping):
            parsed = {}
        return cls(
            price_table=cast(Mapping[str, Mapping[str, Any]], parsed),
            currency=currency,
            enabled=enabled,
        )

    def calculate(
        self,
        *,
        provider: str | None,
        model: str | None,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> dict[str, float | str]:
        if not self.enabled:
            return {
                "input_cost": 0.0,
                "output_cost": 0.0,
                "total_cost": 0.0,
                "currency": self.currency,
            }

        price = self._lookup_price(provider=provider, model=model)
        input_cost = (max(prompt_tokens, 0) / 1_000_000) * price.get(
            "input_per_1m", 0.0
        )
        output_cost = (max(completion_tokens, 0) / 1_000_000) * price.get(
            "output_per_1m", 0.0
        )
        return {
            "input_cost": input_cost,
            "output_cost": output_cost,
            "total_cost": input_cost + output_cost,
            "currency": self.currency,
        }

    def _lookup_price(
        self, *, provider: str | None, model: str | None
    ) -> dict[str, float]:
        provider_key = (provider or "").strip().lower()
        model_key = (model or "").strip().lower()
        candidates = []
        if provider_key and model_key:
            candidates.append(f"{provider_key}:{model_key}")
        if model_key:
            candidates.append(model_key)
        for candidate in candidates:
            if candidate in self._price_table:
                return self._price_table[candidate]
        return {"input_per_1m": 0.0, "output_per_1m": 0.0}
