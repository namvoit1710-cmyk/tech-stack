import pytest
from unittest.mock import MagicMock
from app.layer4_frameworks.providers.odata.polars_odata_provider import PolarsODataProvider

@pytest.mark.unit
class TestPolarsODataProvider:
    def setup_method(self):
        self.provider = PolarsODataProvider()

    def test_parse_query_string_empty(self):
        """Test parsing empty query string"""
        result = self.provider._parse_query_string("")
        assert result == {}

    def test_parse_query_string_simple(self):
        """Test parsing simple query string"""
        result = self.provider._parse_query_string("filter=name eq 'test'&top=10")
        assert result["filter"] == "name eq 'test'"
        assert result["top"] == "10"

    def test_parse_query_string_url_encoded(self):
        """Test parsing URL encoded query string"""
        result = self.provider._parse_query_string("filter=name%20eq%20%27test%27")
        assert result["filter"] == "name eq 'test'"

    def test_parse_filter_simple_eq(self):
        """Test parsing simple equality filter"""
        df_mock = MagicMock()
        df_mock.columns = ["name", "age"]

        result = self.provider._parse_filter("name eq 'test'", df_mock)
        assert result is not None  # Should return a polars expression

    def test_parse_filter_invalid_column(self):
        """Test parsing filter with invalid column"""
        df_mock = MagicMock()
        df_mock.columns = ["name", "age"]

        result = self.provider._parse_filter("invalid_column eq 'test'", df_mock)
        assert result is None

    def test_parse_orderby_simple(self):
        """Test parsing simple orderby"""
        df_mock = MagicMock()
        df_mock.columns = ["name", "age"]

        cols, desc = self.provider._parse_orderby("name", df_mock)
        assert cols == ["name"]
        assert desc == [False]

    def test_parse_orderby_descending(self):
        """Test parsing descending orderby"""
        df_mock = MagicMock()
        df_mock.columns = ["name", "age"]

        cols, desc = self.provider._parse_orderby("name desc", df_mock)
        assert cols == ["name"]
        assert desc == [True]

    def test_parse_and_format_basic(self):
        """Test basic parse and format functionality"""
        df_mock = MagicMock()
        df_mock.columns = ["name", "age"]
        df_mock.__len__ = MagicMock(return_value=2)
        df_mock.filter.return_value = df_mock
        df_mock.select.return_value = df_mock
        df_mock.to_dicts.return_value = [{"name": "test", "age": 25}]

        result = self.provider.parse_and_format(df_mock, "")

        assert "data" in result
        assert "count" in result
        assert "total_count" in result
        assert result["total_count"] == 2  # len(df_mock) returns 2