from app.layer1_domain.value_objects.code_language import CodeLanguage
from app.layer1_domain.value_objects.execution_mode import ExecutionMode


class TestCodeLanguage:
    def test_values(self):
        assert CodeLanguage.JAVASCRIPT.value == "javascript"
        assert CodeLanguage.PYTHON.value == "python"

    def test_membership(self):
        values = {e.value for e in CodeLanguage}
        assert values == {"javascript", "python"}


class TestExecutionMode:
    def test_values(self):
        assert ExecutionMode.ALL_ITEMS.value == "all_items"
        assert ExecutionMode.PER_ITEM.value == "per_item"
