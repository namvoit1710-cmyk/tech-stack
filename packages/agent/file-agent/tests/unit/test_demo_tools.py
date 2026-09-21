from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

# Leftover demo tools the planner LLM can still select: plus, minus,
# get_weather_today. In-scope because they remain in the tool_registry.
# Grounded in app/layer2_application/features/{plus,minus,get_weather_today}.py.


# --- plus -----------------------------------------------------------------


def test_plus_happy():
    from app.layer2_application.features.plus import plus

    # Note: LangChain coerces the float-typed args -> results are floats.
    assert plus.invoke({"left": 2, "right": 3}) == {
        "operation": "plus",
        "left": 2.0,
        "right": 3.0,
        "result": 5.0,
    }


def test_plus_negative_and_zero_boundary():
    from app.layer2_application.features.plus import plus

    assert plus.invoke({"left": -1.5, "right": 0})["result"] == -1.5


# --- minus ----------------------------------------------------------------


def test_minus_happy():
    from app.layer2_application.features.minus import minus

    assert minus.invoke({"left": 10, "right": 4}) == {
        "operation": "minus",
        "left": 10.0,
        "right": 4.0,
        "result": 6.0,
    }


def test_minus_negative_result_edge():
    from app.layer2_application.features.minus import minus

    assert minus.invoke({"left": 1, "right": 4})["result"] == -3.0


# --- get_weather_today ----------------------------------------------------


@pytest.mark.asyncio
async def test_get_weather_today_happy(monkeypatch):
    from app.layer2_application.features import get_weather_today as mod

    llm = SimpleNamespace()
    llm.get_chat_completion = AsyncMock(return_value='{"temp": "hot"}')
    monkeypatch.setattr(mod, "OpenAIService", lambda: llm)

    result = await mod.get_weather_today.ainvoke({"long_input": "weather?"})
    assert result == {"result": '{"temp": "hot"}'}


@pytest.mark.asyncio
async def test_get_weather_today_none_input_boundary(monkeypatch):
    from app.layer2_application.features import get_weather_today as mod

    llm = SimpleNamespace()
    llm.get_chat_completion = AsyncMock(return_value="{}")
    monkeypatch.setattr(mod, "OpenAIService", lambda: llm)

    # long_input defaults to None; source still calls the LLM.
    result = await mod.get_weather_today.ainvoke({})
    assert result == {"result": "{}"}


@pytest.mark.asyncio
async def test_get_weather_today_llm_failure_returns_error(monkeypatch):
    from app.layer2_application.features import get_weather_today as mod

    llm = SimpleNamespace()
    llm.get_chat_completion = AsyncMock(side_effect=RuntimeError("no api"))
    monkeypatch.setattr(mod, "OpenAIService", lambda: llm)

    result = await mod.get_weather_today.ainvoke({"long_input": "x"})
    # source except-branch: {"status": "error", "reason": str(e)}
    assert result == {"status": "error", "reason": "no api"}
