from app.layer1_domain.entities.orchestrator.turn_meter import (
    TurnMeter,
    get_turn_meter,
    reset_turn,
    start_turn,
)


def test_no_active_turn_by_default() -> None:
    assert get_turn_meter() is None


def test_start_turn_makes_meter_visible_and_reset_clears_it() -> None:
    meter = TurnMeter.start_now(message_id="msg-1", conv_id="conv-1")
    token = start_turn(meter)
    try:
        assert get_turn_meter() is meter
    finally:
        reset_turn(token)
    assert get_turn_meter() is None


def test_record_call_accumulates_tokens_cumulatively() -> None:
    meter = TurnMeter.start_now(message_id="msg-1", conv_id="conv-1")

    meter.record_call(820)
    meter.record_call(862)

    assert meter.total_tokens == 1682


def test_record_call_ignores_missing_tokens() -> None:
    meter = TurnMeter.start_now(message_id="msg-1", conv_id="conv-1")

    meter.record_call(None)

    assert meter.total_tokens is None


def test_render_processing_and_success_strings() -> None:
    meter = TurnMeter(message_id="msg-1", conv_id="conv-1", start_perf=0.0, total_tokens=1682)

    processing = meter.render(done=False)
    success = meter.render(done=True)

    assert processing.startswith("Cooking… ")
    assert "· 1,682 tokens" in processing
    assert success.startswith("✻ Cooked for ")
    assert ", 1,682 tokens" in success


def test_render_without_tokens_omits_token_count() -> None:
    meter = TurnMeter(message_id="msg-1", conv_id="conv-1", start_perf=0.0, total_tokens=None)

    assert "tokens" not in meter.render(done=False)
    assert "tokens" not in meter.render(done=True)
