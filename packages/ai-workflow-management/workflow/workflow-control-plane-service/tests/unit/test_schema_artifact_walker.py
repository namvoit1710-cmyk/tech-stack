"""Unit tests for the auto-form schema → artifact walker."""
from __future__ import annotations

from app.layer1_domain.value_objects.auto_form_schema import AutoFormSchema


def _table(key: str, columns: list[str], nested: list[dict] | None = None) -> dict:
    """Minimal auto-form table field shape."""
    cells = [
        {
            "key": col,
            "outputType": "string",
            "fieldConfig": {"fieldWrapper": "TableCellWrapper"},
        }
        for col in columns
    ]
    if nested:
        cells.extend(nested)
    return {
        "key": key,
        "outputType": "array",
        "fieldConfig": {"fieldWrapper": "TableWrapper"},
        "fields": [
            {
                "key": "0",
                "outputType": "object",
                "fieldConfig": {"fieldWrapper": "TableRowWrapper"},
                "fields": cells,
            },
        ],
    }


def _obj(key: str, children: list[dict]) -> dict:
    return {
        "key": key,
        "outputType": "object",
        "fieldConfig": {"fieldWrapper": "FragmentWrapper"},
        "fields": children,
    }


def test_empty_schema_yields_no_artifacts():
    assert AutoFormSchema([]).artifacts() == []
    assert AutoFormSchema(None).artifacts() == []


def test_scalar_only_schema_yields_no_artifacts():
    schema = [
        {"key": "name", "outputType": "string", "fieldConfig": {}},
        {"key": "age", "outputType": "number", "fieldConfig": {}},
    ]
    assert AutoFormSchema(schema).artifacts() == []


def test_root_table_becomes_flat_artifact():
    schema = [_table("orders", ["sku", "qty"])]
    [art] = AutoFormSchema(schema).artifacts()
    assert art.artifact_id == "orders"
    assert art.name == "orders"
    assert art.columns == ["sku", "qty"]
    assert art.depth == 0


def test_object_wraps_table_with_dotted_artifact_id():
    schema = [_obj("customer", [_table("orders", ["sku"])])]
    [art] = AutoFormSchema(schema).artifacts()
    assert art.artifact_id == "customer.orders"
    assert art.name == "orders"
    assert art.depth == 0  # object is passthrough, not depth-bumping


def test_nested_table_inside_tablecell_is_nested_artifact():
    inner = _table("line_items", ["price"])
    schema = [_table("orders", ["sku"], nested=[inner])]
    arts = AutoFormSchema(schema).artifacts()
    ids = {a.artifact_id: a for a in arts}
    assert ids["orders"].depth == 0
    assert ids["orders.line_items"].depth == 1
    assert ids["orders.line_items"].columns == ["price"]


def test_multiple_root_tables():
    schema = [_table("a", ["x"]), _table("b", ["y"])]
    ids = [a.artifact_id for a in AutoFormSchema(schema).artifacts()]
    assert ids == ["a", "b"]


def test_odata_wrapper_with_fragment_row_detected_by_output_type():
    """Detection is driven by ``outputType``, not ``fieldWrapper``. A schema
    using ``OdataWrapper`` + ``FragmentWrapper`` + ``FormItemWrapper`` must
    map to an artifact exactly like the ``TableWrapper``-family equivalent."""
    schema = [
        {
            "key": "orders",
            "outputType": "array",
            "fieldConfig": {"fieldWrapper": "OdataWrapper"},
            "fields": [
                {
                    "key": "orders_row",
                    "outputType": "object",
                    "fieldConfig": {"fieldWrapper": "FragmentWrapper"},
                    "fields": [
                        {"key": "sku", "outputType": "string",
                         "fieldConfig": {"fieldWrapper": "FormItemWrapper"}},
                        {"key": "qty", "outputType": "number",
                         "fieldConfig": {"fieldWrapper": "FormItemWrapper"}},
                    ],
                },
            ],
        },
    ]
    [art] = AutoFormSchema(schema).artifacts()
    assert art.artifact_id == "orders"
    assert art.columns == ["sku", "qty"]
    assert art.depth == 0


def test_odata_wrapper_nested_inside_table_row_is_nested_artifact():
    """Nested array (regardless of wrapper) inside a row cell becomes a
    nested artifact with dotted id."""
    inner = {
        "key": "line_items",
        "outputType": "array",
        "fieldConfig": {"fieldWrapper": "OdataWrapper"},
        "fields": [{
            "key": "li_row", "outputType": "object",
            "fieldConfig": {"fieldWrapper": "FragmentWrapper"},
            "fields": [
                {"key": "price", "outputType": "number",
                 "fieldConfig": {"fieldWrapper": "FormItemWrapper"}},
            ],
        }],
    }
    schema = [_table("orders", ["sku"], nested=[inner])]
    arts = AutoFormSchema(schema).artifacts()
    ids = {a.artifact_id: a for a in arts}
    assert "orders" in ids
    assert "orders.line_items" in ids
    assert ids["orders.line_items"].columns == ["price"]
    assert ids["orders.line_items"].depth == 1


def test_top_level_array_default_rows_captured():
    """A top-level ``array`` field with a ``default`` list flows into the
    artifact's ``default_rows``."""
    schema = [_table("tblProcess", ["stepId", "description"])]
    schema[0]["default"] = [
        {"stepId": 1, "description": "first"},
        {"stepId": 2, "description": "second"},
    ]
    arts = AutoFormSchema(schema).artifacts()
    assert len(arts) == 1
    assert arts[0].default_rows == [
        {"stepId": 1, "description": "first"},
        {"stepId": 2, "description": "second"},
    ]


def test_array_without_default_has_empty_default_rows():
    schema = [_table("orders", ["sku"])]
    arts = AutoFormSchema(schema).artifacts()
    assert arts[0].default_rows == []


def test_array_default_ignores_non_dict_entries():
    """Stray scalar/None entries inside ``default`` are filtered out so the
    provisioner never tries to write a non-row to CSV."""
    schema = [_table("orders", ["sku"])]
    schema[0]["default"] = [{"sku": "A1"}, "not a dict", None, {"sku": "B2"}]
    arts = AutoFormSchema(schema).artifacts()
    assert arts[0].default_rows == [{"sku": "A1"}, {"sku": "B2"}]


def _scalar(key: str, default=None) -> dict:
    f = {"key": key, "outputType": "string",
         "fieldConfig": {"fieldWrapper": "FormItemWrapper"}}
    if default is not None:
        f["default"] = default
    return f


def test_object_with_scalar_children_emits_no_artifact():
    """Object containers never mint their own artifact — their scalars
    land in the ``__root__`` CSV as dotted columns instead."""
    schema = [_obj("tabs", [
        _obj("tab_1", [_scalar("templateName", "Acme"), _scalar("status", "ACTIVE")]),
    ])]
    assert AutoFormSchema(schema).artifacts() == []
    assert AutoFormSchema(schema).scalar_columns() == [
        "tabs.tab_1.templateName", "tabs.tab_1.status",
    ]
    assert AutoFormSchema(schema).scalar_defaults() == {
        "tabs.tab_1.templateName": "Acme",
        "tabs.tab_1.status": "ACTIVE",
    }


def test_nested_scalars_without_defaults_still_become_root_columns():
    schema = [_obj("section", [_scalar("a"), _scalar("b")])]
    assert AutoFormSchema(schema).artifacts() == []
    assert AutoFormSchema(schema).scalar_columns() == ["section.a", "section.b"]
    assert AutoFormSchema(schema).scalar_defaults() == {}  # no defaults → header-only


def test_sibling_tabs_reuse_leaf_key_without_collision():
    """``created_at`` in two tabs → two distinct dotted root columns; a
    flat key could not represent both."""
    schema = [_obj("tabs", [
        _obj("tab_1", [_scalar("created_at", "t1")]),
        _obj("tab_2", [_scalar("created_at", "t2")]),
    ])]
    assert AutoFormSchema(schema).scalar_columns() == [
        "tabs.tab_1.created_at", "tabs.tab_2.created_at",
    ]
    assert AutoFormSchema(schema).scalar_defaults() == {
        "tabs.tab_1.created_at": "t1",
        "tabs.tab_2.created_at": "t2",
    }


def test_object_wrapping_only_table_emits_only_the_table():
    """A passthrough object that wraps only a table contributes no root
    columns — only the table artifact is emitted."""
    schema = [_obj("customer", [_table("orders", ["sku"])])]
    [art] = AutoFormSchema(schema).artifacts()
    assert art.artifact_id == "customer.orders"
    assert AutoFormSchema(schema).scalar_columns() == []


def test_object_with_scalars_and_table_splits_root_columns_from_artifact():
    schema = [_obj("tab", [_scalar("title", "T"), _table("rows", ["a"])])]
    [art] = AutoFormSchema(schema).artifacts()
    assert art.artifact_id == "tab.rows"
    assert AutoFormSchema(schema).scalar_columns() == ["tab.title"]
    assert AutoFormSchema(schema).scalar_defaults() == {"tab.title": "T"}


def test_root_scalar_defaults_extracted():
    schema = [
        {"key": "name", "outputType": "string", "default": "alice",
         "fieldConfig": {}},
        {"key": "count", "outputType": "number", "default": 7,
         "fieldConfig": {}},
        {"key": "active", "outputType": "boolean", "fieldConfig": {}},
    ]
    assert AutoFormSchema(schema).scalar_defaults() == {"name": "alice", "count": 7}


def test_root_scalar_defaults_skip_arrays_and_scalarless_objects():
    """Array defaults belong on their artifact; an object container with no
    scalar descendants contributes nothing."""
    schema = [
        _table("orders", ["sku"]),
        _obj("nested", []),
    ]
    schema[0]["default"] = [{"sku": "A"}]
    schema[1]["default"] = {"foo": "bar"}
    assert AutoFormSchema(schema).scalar_defaults() == {}


def test_scalars_inside_array_rows_are_not_root_columns():
    """Table cells materialise as the array artifact's columns, never as
    root columns — even though they are scalar-typed fields."""
    schema = [_table("orders", ["sku", "qty"])]
    assert AutoFormSchema(schema).scalar_columns() == []
