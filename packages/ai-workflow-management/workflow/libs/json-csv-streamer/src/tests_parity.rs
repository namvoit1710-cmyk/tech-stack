//! SA-1905 F1/F2 — Rust-side unit tests for the SA-1977 parity surface.
//!
//! These exist because the 13 Python tests in the control plane
//! (`tests/unit/test_json_csv_streamer.py`) open with
//! `pytest.importorskip("json_csv_streamer")` and CI never builds the wheel, so
//! they skip silently and CI goes green with zero coverage of `lib.rs`. These
//! need no Python and no wheel — `cargo test` runs them.
//!
//! They target the functions whose behaviour must match `value_kind.py` /
//! `CsvConverter` on the Python side. Those agreements were documented in
//! comments and enforced by nothing.

use super::*;
use serde_json::json;

/// A `Schema` carrying only the given per-line root kinds.
fn kinds(ks: &[&'static str]) -> Schema {
    let mut s = Schema::new();
    for k in ks {
        s.item_kinds.insert(k);
    }
    s
}

fn flatten(v: &Value) -> Vec<(String, String, Option<&'static str>)> {
    let mut out = Vec::new();
    match v {
        Value::Object(m) => flatten_object("", m, &mut out),
        _ => panic!("flatten() takes an object"),
    }
    out
}

fn cell<'a>(
    out: &'a [(String, String, Option<&'static str>)],
    key: &str,
) -> (&'a str, Option<&'static str>) {
    out.iter()
        .find(|(k, _, _)| k == key)
        .map(|(_, c, t)| (c.as_str(), *t))
        .unwrap_or_else(|| panic!("missing column {key}"))
}

fn tmpdir(tag: &str) -> std::path::PathBuf {
    let d = std::env::temp_dir().join(format!("jcs-test-{tag}"));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

// ---- root_kind -----------------------------------------------------------

#[test]
fn root_kind_covers_every_json_variant() {
    assert_eq!(root_kind(&json!({})), K_OBJECT);
    assert_eq!(root_kind(&json!([])), K_ARRAY);
    assert_eq!(root_kind(&json!("s")), K_STRING);
    assert_eq!(root_kind(&json!(1)), K_NUMBER);
    assert_eq!(root_kind(&json!(1.5)), K_NUMBER);
    assert_eq!(root_kind(&json!(true)), K_BOOLEAN);
    assert_eq!(root_kind(&Value::Null), K_NULL);
}

// ---- flatten_object ------------------------------------------------------

#[test]
fn flatten_dots_nested_objects_and_json_encodes_arrays() {
    let out = flatten(&json!({
        "sku": "A", "qty": 3, "ok": true, "gone": null,
        "meta": {"warehouse": "W1", "deep": {"x": 1}},
        "tags": ["a", "b"]
    }));

    assert_eq!(cell(&out, "sku"), ("A", Some("string")));
    assert_eq!(cell(&out, "qty"), ("3", Some("number")));
    assert_eq!(cell(&out, "ok"), ("true", Some("boolean")));
    // null leaves the column type unlocked so a later non-null row can decide it
    assert_eq!(cell(&out, "gone"), ("", None));
    assert_eq!(cell(&out, "meta.warehouse"), ("W1", Some("string")));
    assert_eq!(cell(&out, "meta.deep.x"), ("1", Some("number")));
    assert_eq!(cell(&out, "tags"), (r#"["a","b"]"#, Some("json")));
    // a branch never becomes a column of its own
    assert!(out.iter().all(|(k, _, _)| k != "meta"));
}

/// Guards the `preserve_order` feature. Without it serde_json sorts keys and the
/// CSV header silently reorders — the same data, a different file.
#[test]
fn flatten_preserves_source_key_order() {
    let v: Value = serde_json::from_str(r#"{"z":1,"a":2,"m":{"q":3,"b":4}}"#).unwrap();
    let out = flatten(&v);
    let keys: Vec<&str> = out.iter().map(|(k, _, _)| k.as_str()).collect();
    assert_eq!(keys, vec!["z", "a", "m.q", "m.b"]);
}

/// Guards `arbitrary_precision` (SA-1977 A5). Without it this integer parses as
/// f64 and writes back as `1e+25`, disagreeing with the Python writer about an id.
#[test]
fn flatten_keeps_big_integers_exact() {
    let v: Value = serde_json::from_str(r#"{"id":10000000000000000000000000}"#).unwrap();
    assert_eq!(
        cell(&flatten(&v), "id"),
        ("10000000000000000000000000", Some("number"))
    );
}

// ---- value_cell ----------------------------------------------------------

#[test]
fn value_cell_plain_form_keeps_scalars_readable() {
    assert_eq!(value_cell(&json!("hi"), false), "hi");
    assert_eq!(value_cell(&json!(3), false), "3");
    assert_eq!(value_cell(&json!(true), false), "true");
    assert_eq!(value_cell(&Value::Null, false), "");
    // structures are JSON-encoded even in plain mode
    assert_eq!(value_cell(&json!([1, 2]), false), "[1,2]");
}

/// In mixed mode every element is JSON-encoded so one declared column type covers
/// them all — and so `null` stays distinct from the empty string. Without this,
/// `["a", null, "b"]` reads back as `["a", "", "b"]`.
#[test]
fn value_cell_mixed_mode_keeps_null_distinct_from_empty() {
    assert_eq!(value_cell(&Value::Null, true), "null");
    assert_eq!(value_cell(&json!(""), true), r#""""#);
    assert_eq!(value_cell(&json!("hi"), true), r#""hi""#);
}

// ---- Schema::observe -----------------------------------------------------

#[test]
fn observe_dedupes_columns_and_first_non_null_type_wins() {
    let mut s = Schema::new();
    s.observe("b", Some("string"));
    s.observe("a", None);
    s.observe("b", Some("number")); // already typed: not overwritten
    s.observe("a", Some("number")); // was unlocked: now decided
    assert_eq!(s.columns, vec!["b", "a"]);
    assert_eq!(s.types["b"], "string");
    assert_eq!(s.types["a"], "number");
}

// ---- Schema::item_kind ---------------------------------------------------

#[test]
fn item_kind_truth_table() {
    assert_eq!(kinds(&[]).item_kind(), K_UNKNOWN); // no lines at all
    assert_eq!(kinds(&[K_OBJECT]).item_kind(), K_OBJECT);
    assert_eq!(kinds(&[K_NULL]).item_kind(), K_NULL);
    assert_eq!(kinds(&[K_STRING]).item_kind(), K_STRING);
    assert_eq!(kinds(&[K_NUMBER]).item_kind(), K_NUMBER);
    assert_eq!(kinds(&[K_ARRAY]).item_kind(), K_ARRAY);
    assert_eq!(kinds(&[K_BOOLEAN]).item_kind(), K_BOOLEAN);
    assert_eq!(kinds(&[K_STRING, K_NULL]).item_kind(), K_STRING);
    assert_eq!(kinds(&[K_ARRAY, K_NULL]).item_kind(), K_ARRAY);
    assert_eq!(kinds(&[K_STRING, K_NUMBER]).item_kind(), K_UNKNOWN);
}

/// The asymmetry the SA-1977 comment calls load-bearing, pinned. A sparse
/// `[{...}, null, {...}]` must NOT be reported as `array<object>`: those lines
/// went to `__value` via `value_mode`, and a reader told `object` would refuse to
/// unwrap what the writer wrapped.
#[test]
fn object_plus_null_is_unknown_not_object() {
    assert_eq!(kinds(&[K_OBJECT, K_NULL]).item_kind(), K_UNKNOWN);
}

// ---- Schema::json_cells --------------------------------------------------

#[test]
fn json_cells_only_for_mixed_or_nullable_strings() {
    assert!(!kinds(&[K_OBJECT]).json_cells());
    assert!(!kinds(&[K_STRING]).json_cells());
    assert!(!kinds(&[K_NUMBER]).json_cells());
    assert!(!kinds(&[K_NULL]).json_cells());
    assert!(kinds(&[K_STRING, K_NULL]).json_cells()); // "" must not swallow null
    assert!(kinds(&[K_STRING, K_NUMBER]).json_cells()); // one type must cover both
    assert!(kinds(&[]).json_cells()); // unknown
}

// ---- Schema::finish ------------------------------------------------------

#[test]
fn finish_object_mode_keeps_the_column_union() {
    let mut s = kinds(&[K_OBJECT]);
    s.observe("a", Some("number"));
    s.observe("b", Some("string"));
    let (cols, types, item, value_mode, json_cells) = s.finish();
    assert_eq!(cols, vec!["a", "b"]);
    assert_eq!(types["a"], "number");
    assert_eq!(types["b"], "string");
    assert_eq!(item, K_OBJECT);
    assert!(!value_mode);
    assert!(!json_cells);
}

/// Object columns seen before the first non-object line must be discarded —
/// keeping them would emit a leaf column and a `__value` column for one file.
#[test]
fn finish_value_mode_discards_object_columns() {
    let mut s = kinds(&[K_NUMBER]);
    s.observe("leftover", Some("number"));
    s.value_mode = true;
    let (cols, types, item, value_mode, json_cells) = s.finish();
    assert_eq!(cols, vec![VALUE_COLUMN]);
    assert_eq!(types[VALUE_COLUMN], "number");
    assert!(!types.contains_key("leftover"));
    assert_eq!(item, K_NUMBER);
    assert!(value_mode);
    assert!(!json_cells);
}

#[test]
fn finish_value_mode_all_null_leaves_the_type_unlocked() {
    let mut s = kinds(&[K_NULL]);
    s.value_mode = true;
    let (cols, types, item, _, json_cells) = s.finish();
    assert_eq!(cols, vec![VALUE_COLUMN]);
    assert!(types.is_empty()); // no declared type -> reads back as None
    assert_eq!(item, K_NULL);
    assert!(!json_cells);
}

#[test]
fn finish_value_mode_types_structures_and_mixed_as_json() {
    let mut arrays = kinds(&[K_ARRAY]);
    arrays.value_mode = true;
    let (_, types, _, _, json_cells) = arrays.finish();
    assert_eq!(types[VALUE_COLUMN], "json");
    assert!(!json_cells);

    let mut mixed = kinds(&[K_STRING, K_NUMBER]);
    mixed.value_mode = true;
    let (_, types, item, _, json_cells) = mixed.finish();
    assert_eq!(item, K_UNKNOWN);
    assert_eq!(types[VALUE_COLUMN], "json");
    assert!(json_cells);
}

// ---- end-to-end, no Python -----------------------------------------------

#[test]
fn sketch_unions_sparse_columns_and_write_fills_the_gaps() {
    let d = tmpdir("sparse");
    let (inp, outp) = (d.join("in.jsonl"), d.join("out.csv"));
    // blank line in the middle: skipped by both passes, so it is not a row
    std::fs::write(&inp, "{\"a\":1}\n\n{\"b\":2}\n").unwrap();

    let schema = sketch(inp.to_str().unwrap()).unwrap();
    assert_eq!(schema.row_count, 2);
    let (cols, _types, item, value_mode, json_cells) = schema.finish();
    assert_eq!(cols, vec!["a", "b"]); // `b` first seen at row 2 still makes the union
    assert_eq!(item, K_OBJECT);

    let n = write_csv_impl(
        inp.to_str().unwrap(),
        outp.to_str().unwrap(),
        &cols,
        json_cells,
        value_mode,
    )
    .unwrap();
    assert_eq!(n, 2);
    assert_eq!(
        std::fs::read_to_string(&outp).unwrap(),
        "__row_id,a,b\n0,1,\n1,,2\n"
    );
    let _ = std::fs::remove_dir_all(&d);
}

#[test]
fn one_non_object_line_puts_the_whole_file_in_value_mode() {
    let d = tmpdir("valuemode");
    let (inp, outp) = (d.join("in.jsonl"), d.join("out.csv"));
    std::fs::write(&inp, "{\"a\":1}\n\"plain\"\n").unwrap();

    let schema = sketch(inp.to_str().unwrap()).unwrap();
    let (cols, _types, item, value_mode, json_cells) = schema.finish();
    assert!(value_mode);
    assert_eq!(cols, vec![VALUE_COLUMN]);
    assert_eq!(item, K_UNKNOWN); // object + string -> neither wins
    assert!(json_cells);

    write_csv_impl(
        inp.to_str().unwrap(),
        outp.to_str().unwrap(),
        &cols,
        json_cells,
        value_mode,
    )
    .unwrap();
    // every cell JSON-encoded, whole lines preserved, nothing flattened
    assert_eq!(
        std::fs::read_to_string(&outp).unwrap(),
        "__row_id,__value\n0,\"{\"\"a\"\":1}\"\n1,\"\"\"plain\"\"\"\n"
    );
    let _ = std::fs::remove_dir_all(&d);
}

#[test]
fn an_invalid_json_line_is_an_error_not_a_skipped_row() {
    let d = tmpdir("badline");
    let inp = d.join("in.jsonl");
    std::fs::write(&inp, "{\"a\":1}\nnot json\n").unwrap();
    assert!(sketch(inp.to_str().unwrap()).is_err());
    let _ = std::fs::remove_dir_all(&d);
}
