"""Tests for the Port value object and its domain rules."""

import pytest

from app.layer1_domain.value_objects.port import Port, has_io_port


class TestHasIoPort:
    def test_true_when_input_port_present(self):
        assert has_io_port({"in": [{"id": "in_default"}], "out": []}) is True

    def test_true_when_output_port_present(self):
        assert has_io_port({"in": [], "out": [{"id": "out_default"}]}) is True

    def test_true_when_both_present(self):
        assert has_io_port({"in": [{"id": "a"}], "out": [{"id": "b"}]}) is True

    def test_false_when_both_empty(self):
        assert has_io_port({"in": [], "out": []}) is False

    def test_false_when_empty_dict(self):
        assert has_io_port({}) is False

    @pytest.mark.parametrize("bad", [None, [], "ports", 0])
    def test_false_when_not_a_dict(self, bad):
        assert has_io_port(bad) is False


class TestPortRoundTrip:
    def test_to_dict_from_dict(self):
        p = Port(id="in_default", label="Input", data_type="json", required=False, description="d")
        assert Port.from_dict(p.to_dict()) == p
