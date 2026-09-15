"""Tests for value objects."""

import pytest

from app.layer1_domain.value_objects.operation_type import OperationType


class TestOperationType:
    """Tests for OperationType enum."""

    def test_values(self):
        """Test enum values."""
        assert OperationType.SEND_MESSAGE == "send_message"
        assert OperationType.SEND_ADAPTIVE_CARD == "send_adaptive_card"

    def test_is_string(self):
        """Test that enum values are strings."""
        assert isinstance(OperationType.SEND_MESSAGE, str)
        assert isinstance(OperationType.SEND_ADAPTIVE_CARD, str)

    def test_from_string_valid(self):
        """Test from_string with valid values."""
        assert OperationType.from_string("send_message") == OperationType.SEND_MESSAGE
        assert (
            OperationType.from_string("send_adaptive_card")
            == OperationType.SEND_ADAPTIVE_CARD
        )

    def test_from_string_case_insensitive(self):
        """Test from_string is case insensitive."""
        assert OperationType.from_string("SEND_MESSAGE") == OperationType.SEND_MESSAGE
        assert OperationType.from_string("Send_Message") == OperationType.SEND_MESSAGE

    def test_from_string_with_whitespace(self):
        """Test from_string strips whitespace."""
        assert (
            OperationType.from_string("  send_message  ") == OperationType.SEND_MESSAGE
        )

    def test_from_string_empty_raises(self):
        """Test from_string raises on empty string."""
        with pytest.raises(ValueError, match="cannot be empty"):
            OperationType.from_string("")

    def test_from_string_invalid_raises(self):
        """Test from_string raises on invalid value."""
        with pytest.raises(ValueError, match="Invalid operation type"):
            OperationType.from_string("invalid")

    def test_display_name(self):
        """Test display_name property."""
        assert OperationType.SEND_MESSAGE.display_name == "Send Message"
        assert OperationType.SEND_ADAPTIVE_CARD.display_name == "Send Adaptive Card"
