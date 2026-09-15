"""Tests for the ``parse_node_ref`` value object."""

import pytest

from app.layer1_domain.value_objects.node_ref import parse_node_ref


class TestParseNodeRef:
    def test_no_version(self):
        assert parse_node_ref("builtin:condition") == ("builtin:condition", None)

    def test_with_version(self):
        assert parse_node_ref("worker:http@1.2.0") == ("worker:http", "1.2.0")

    def test_business_with_version(self):
        assert parse_node_ref("business:abc@0.9.1") == ("business:abc", "0.9.1")

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            parse_node_ref("")

    def test_multiple_at_raises(self):
        with pytest.raises(ValueError, match="too many"):
            parse_node_ref("a@b@c")

    def test_empty_id_before_at_raises(self):
        with pytest.raises(ValueError, match="id cannot be empty"):
            parse_node_ref("@1.0.0")

    def test_empty_version_after_at_raises(self):
        with pytest.raises(ValueError, match="version cannot be empty"):
            parse_node_ref("builtin:x@")
