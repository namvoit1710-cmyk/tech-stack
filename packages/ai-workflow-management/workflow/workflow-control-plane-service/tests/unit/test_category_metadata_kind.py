from app.layer2_application.providers.category_metadata import get_category_info


def test_leaf_collection_kind_uses_kind_label_and_order():
    assert get_category_info("Jira.read") == ("Find & Read", 3)
    assert get_category_info("Jira.action") == ("Actions", 2)
    assert get_category_info("Core.util") == ("Utilities", 7)
    assert get_category_info("My Nodes.trigger") == ("Triggers", 1)


def test_top_level_collection_order():
    assert get_category_info("Core") == ("Core", 0)
    assert get_category_info("My Nodes") == ("My Nodes", 90)
    assert get_category_info("Jira") == ("Jira", 10)
