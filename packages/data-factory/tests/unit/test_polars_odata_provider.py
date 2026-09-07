import pytest
import polars as pl
from app.layer4_frameworks.providers.odata.polars_odata_provider import PolarsODataProvider


class TestPolarsODataProvider:

    @pytest.fixture
    def sample_dataframe(self):
        """Create a sample DataFrame for testing."""
        return pl.DataFrame({
            'name': ['John Doe', 'Jane Smith', 'Bob Johnson', 'Alice Brown'],
            'email': ['john@example.com', 'jane@example.com', 'bob@catherinegates.com', 'alice@example.com'],
            'phone': ['123-456-7890', '098-765-4321', '555-123-4567', '111-222-3333'],
            'age': [25, 30, 35, 28],
            'active': [True, False, True, True]
        })

    @pytest.fixture
    def provider(self):
        """Create a PolarsODataProvider instance."""
        return PolarsODataProvider()

    def test_parse_and_format_no_filter(self, provider, sample_dataframe):
        """Test parsing without filters."""
        result = provider.parse_and_format(sample_dataframe, "")

        assert result['total_count'] == 4
        assert len(result['data']) == 4
        assert result['data'][0]['name'] == 'John Doe'

    def test_parse_and_format_with_contains(self, provider, sample_dataframe):
        """Test contains function filtering."""
        result = provider.parse_and_format(sample_dataframe, "$filter=contains(email,'catherinegates')")

        assert result['total_count'] == 1
        assert len(result['data']) == 1
        assert result['data'][0]['email'] == 'bob@catherinegates.com'

    def test_parse_and_format_with_or_contains(self, provider, sample_dataframe):
        """Test OR logic with contains functions (the main fix)."""
        result = provider.parse_and_format(sample_dataframe, "$filter=contains(name,'John') or contains(email,'catherinegates')")

        assert result['total_count'] == 2  # John Doe and Bob Johnson
        assert len(result['data']) == 2

        names = [record['name'] for record in result['data']]
        assert 'John Doe' in names
        assert 'Bob Johnson' in names

    def test_parse_and_format_complex_or_query(self, provider, sample_dataframe):
        """Test complex OR query with multiple contains functions."""
        result = provider.parse_and_format(
            sample_dataframe,
            "$filter=contains(name,'catherinegates') or contains(phone,'catherinegates') or contains(email,'catherinegates') or contains(name,'catherinegates')"
        )

        # Should find Bob Johnson (email contains 'catherinegates')
        assert result['total_count'] == 1
        assert result['data'][0]['email'] == 'bob@catherinegates.com'

    def test_parse_and_format_with_startswith(self, provider, sample_dataframe):
        """Test startswith function."""
        result = provider.parse_and_format(sample_dataframe, "$filter=startswith(name,'John')")

        assert result['total_count'] == 1
        assert result['data'][0]['name'] == 'John Doe'

    def test_parse_and_format_with_endswith(self, provider, sample_dataframe):
        """Test endswith function."""
        result = provider.parse_and_format(sample_dataframe, "$filter=endswith(name,'Smith')")

        assert result['total_count'] == 1
        assert result['data'][0]['name'] == 'Jane Smith'

    def test_parse_and_format_with_eq_operator(self, provider, sample_dataframe):
        """Test equality operator."""
        result = provider.parse_and_format(sample_dataframe, "$filter=age eq 25")

        assert result['total_count'] == 1
        assert result['data'][0]['age'] == 25

    def test_parse_and_format_with_gt_operator(self, provider, sample_dataframe):
        """Test greater than operator."""
        result = provider.parse_and_format(sample_dataframe, "$filter=age gt 30")

        assert result['total_count'] == 1
        assert result['data'][0]['age'] == 35

    def test_parse_and_format_with_boolean_field(self, provider, sample_dataframe):
        """Test filtering on boolean fields."""
        result = provider.parse_and_format(sample_dataframe, "$filter=active eq true")

        assert result['total_count'] == 3  # John, Bob, Alice are active

    def test_parse_and_format_with_and_logic(self, provider, sample_dataframe):
        """Test AND logic."""
        result = provider.parse_and_format(sample_dataframe, "$filter=age gt 25 and active eq true")

        assert result['total_count'] == 2  # Bob (35) and Alice (28) are both >25 and active

    def test_parse_and_format_with_top(self, provider, sample_dataframe):
        """Test $top parameter."""
        result = provider.parse_and_format(sample_dataframe, "$top=2")

        assert result['total_count'] == 4  # Total count is still 4
        assert len(result['data']) == 2   # But only 2 records returned

    def test_parse_and_format_with_skip(self, provider, sample_dataframe):
        """Test $skip parameter."""
        result = provider.parse_and_format(sample_dataframe, "$skip=2")

        assert result['total_count'] == 4
        assert len(result['data']) == 2
        assert result['data'][0]['name'] == 'Bob Johnson'  # Skipped first 2

    def test_parse_and_format_with_orderby(self, provider, sample_dataframe):
        """Test $orderby parameter."""
        result = provider.parse_and_format(sample_dataframe, "$orderby=name asc")

        assert result['total_count'] == 4
        assert result['data'][0]['name'] == 'Alice Brown'
        assert result['data'][1]['name'] == 'Bob Johnson'

    def test_parse_and_format_with_select(self, provider, sample_dataframe):
        """Test $select parameter."""
        result = provider.parse_and_format(sample_dataframe, "$select=name,email")

        assert result['total_count'] == 4
        # Check that only selected fields are present
        record = result['data'][0]
        assert 'name' in record
        assert 'email' in record
        assert 'phone' not in record
        assert 'age' not in record

    def test_parse_and_format_with_count(self, provider, sample_dataframe):
        """Test $count parameter."""
        result = provider.parse_and_format(sample_dataframe, "$count=true")

        assert '@odata.count' in result
        assert result['@odata.count'] == 4

    def test_parse_and_format_combined_parameters(self, provider, sample_dataframe):
        """Test combining multiple parameters."""
        result = provider.parse_and_format(
            sample_dataframe,
            "$filter=contains(name,'John')&$orderby=name asc&$top=1&$count=true"
        )

        assert result['@odata.count'] == 2  # Total matching records
        assert len(result['data']) == 1     # But only 1 returned due to $top
        assert result['data'][0]['name'] == 'Bob Johnson'  # First in alphabetical order

    def test_parse_and_format_invalid_column(self, provider, sample_dataframe):
        """Test filtering on non-existent column."""
        result = provider.parse_and_format(sample_dataframe, "$filter=contains(nonexistent,'value')")

        # Should return empty result when column doesn't exist
        assert result['total_count'] == 0
        assert len(result['data']) == 0

    def test_parse_and_format_malformed_query(self, provider, sample_dataframe):
        """Test handling of malformed queries."""
        result = provider.parse_and_format(sample_dataframe, "$filter=invalid syntax")

        # Should handle gracefully and return all data
        assert result['total_count'] == 4
        assert len(result['data']) == 4