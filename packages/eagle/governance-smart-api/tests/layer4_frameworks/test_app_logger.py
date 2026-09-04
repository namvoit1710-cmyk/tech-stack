import logging

from app.layer4_frameworks.logger.app_logger import AppLogger


def test_app_logger_emits_plain_message(caplog) -> None:
    logger = AppLogger(name="test_app_logger_plain", level="INFO")

    with caplog.at_level(logging.INFO, logger="test_app_logger_plain"):
        logger.info("hello world")

    assert "hello world" in caplog.text


def test_app_logger_appends_context_suffix(caplog) -> None:
    logger = AppLogger(name="test_app_logger_ctx", level="INFO")

    with caplog.at_level(logging.INFO, logger="test_app_logger_ctx"):
        logger.info("processed", tenant="acme", rows=3)

    # Context is appended as key=repr(value) pairs.
    assert "processed" in caplog.text
    assert "tenant='acme'" in caplog.text
    assert "rows=3" in caplog.text


def test_app_logger_falls_back_to_info_for_unknown_level() -> None:
    AppLogger(name="test_app_logger_bad_level", level="NOTALEVEL")

    assert logging.getLogger("test_app_logger_bad_level").level == logging.INFO


def test_app_logger_accepts_lowercase_level() -> None:
    # EDGE: level is upper-cased before lookup, so lowercase input still resolves.
    AppLogger(name="test_app_logger_lower", level="debug")

    assert logging.getLogger("test_app_logger_lower").level == logging.DEBUG


def test_app_logger_info_without_context_has_no_suffix(caplog) -> None:
    # BOUNDARY: empty context -> the key=value suffix branch is skipped.
    logger = AppLogger(name="test_app_logger_nosuffix", level="INFO")

    with caplog.at_level(logging.INFO, logger="test_app_logger_nosuffix"):
        logger.info("bare message")

    assert caplog.records[-1].getMessage() == "bare message"
