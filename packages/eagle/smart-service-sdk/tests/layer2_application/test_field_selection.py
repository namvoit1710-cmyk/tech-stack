from smart_service_sdk.layer2_application.features.field_configuration.field_selection import (
    compose_field_text,
    resolve_included_fields,
    unknown_fields,
)


def test_resolve_empty_selection_returns_all_in_given_order() -> None:
    assert resolve_included_fields(["b", "a", "c"], []) == ["b", "a", "c"]


def test_resolve_selection_orders_by_selection_and_drops_absent() -> None:
    # 'x' is not on the row -> skipped; order follows the selection, not the row
    assert resolve_included_fields(["a", "b", "c"], ["c", "x", "a"]) == ["c", "a"]


def test_compose_field_text_joins_only_selected_fields() -> None:
    fields = {"a": "1", "b": "2", "c": "3"}
    assert compose_field_text(fields, ["c", "a"], separator=" | ") == "c: 3 | a: 1"


def test_compose_field_text_skips_absent_field() -> None:
    assert compose_field_text({"a": "1"}, ["a", "b"], separator="\n") == "a: 1"


def test_unknown_fields_is_sorted_and_deduped() -> None:
    assert unknown_fields(["x", "a", "x"], ["a", "b"]) == ["x"]
    assert unknown_fields(["a", "b"], ["a", "b"]) == []


# --- SA-1451 correctness hardening (messy real-world CSV headers) ---


def test_match_is_tolerant_of_header_whitespace() -> None:
    # header carries a stray trailing space; the clean config must still match,
    # and the ACTUAL header (with its spacing) is returned for value lookup.
    assert resolve_included_fields(["Material Name ", "Manufacturer"], ["Material Name"]) == [
        "Material Name "
    ]


def test_match_is_case_insensitive() -> None:
    assert resolve_included_fields(["Material Name", "Manufacturer"], ["material name"]) == [
        "Material Name"
    ]


def test_embedding_falls_back_to_all_when_selection_matches_nothing() -> None:
    # a row that carries none of the configured fields must not embed empty
    assert resolve_included_fields(
        ["Material Name", "Manufacturer"], ["Ghost"], fallback_to_all_when_empty=True
    ) == ["Material Name", "Manufacturer"]


def test_graph_does_not_fall_back_when_selection_matches_nothing() -> None:
    # default (graph) behavior: no match -> no fields -> row contributes no entities
    assert resolve_included_fields(["Material Name", "Manufacturer"], ["Ghost"]) == []


def test_unknown_fields_normalizes_case_and_whitespace() -> None:
    # 'material name' (lower) matches header 'Material Name '; only real strangers reported
    assert unknown_fields(["material name", "Ghost"], ["Material Name ", "Manufacturer"]) == [
        "Ghost"
    ]
