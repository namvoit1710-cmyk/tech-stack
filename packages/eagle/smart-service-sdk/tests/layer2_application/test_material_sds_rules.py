from smart_service_sdk.layer2_application.features.material_sds_analysis.material_sds_rules import (
    normalize,
    screen_text,
)


def _categories(text):
    return {match.category for match in screen_text(text)}


def test_detects_solvent_category():
    assert "solvents" in _categories("Acetone-based industrial degreaser")


def test_detects_battery_category_across_hyphen_and_case():
    assert "batteries" in _categories("Rechargeable LI-ION Battery Module")


def test_multiword_term_is_matched():
    assert "compressed_gases" in _categories("Portable compressed gas canister")


def test_word_boundary_avoids_false_positive():
    # "gasket" must not trigger the "gas" term.
    assert "gases" not in _categories("Rubber gasket for pump flange")


def test_no_hazard_returns_empty():
    assert screen_text("Stainless steel hex bolt 3/8 inch") == []


def test_blank_text_returns_empty():
    assert screen_text("") == []
    assert screen_text("   ") == []


def test_multiple_categories_are_deduped_one_per_category():
    matches = screen_text("Acetone and toluene solvent blend")
    solvent_hits = [m for m in matches if m.category == "solvents"]
    assert len(solvent_hits) == 1


def test_normalize_lowercases_and_collapses():
    assert normalize("  Li-ION   Battery ") == "li ion battery"
