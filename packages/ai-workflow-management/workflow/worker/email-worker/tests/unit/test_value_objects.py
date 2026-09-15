"""Tests for Email worker value objects."""

import pytest

from app.layer1_domain.value_objects.operation_type import OperationType


class TestOperationType:
    """Tests for OperationType enum."""

    def test_from_string_valid(self):
        """Test conversion of valid operation type strings."""
        assert OperationType.from_string("send_smtp") == OperationType.SEND_SMTP
        assert OperationType.from_string("send_graph") == OperationType.SEND_GRAPH

    def test_from_string_normalizes_case_and_whitespace(self):
        """Test that case and surrounding whitespace are normalized."""
        assert OperationType.from_string("  SEND_SMTP ") == OperationType.SEND_SMTP
        assert OperationType.from_string("Send_Graph") == OperationType.SEND_GRAPH

    def test_from_string_empty_raises(self):
        """Test that an empty value raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            OperationType.from_string("")

    def test_from_string_invalid_raises(self):
        """Test that an invalid value raises ValueError listing valid types."""
        with pytest.raises(ValueError, match="Valid types are"):
            OperationType.from_string("send_pigeon")

    def test_display_name(self):
        """Test human-readable display names."""
        assert OperationType.SEND_SMTP.display_name == "Send via SMTP"
        assert OperationType.SEND_GRAPH.display_name == "Send via Microsoft Graph"
