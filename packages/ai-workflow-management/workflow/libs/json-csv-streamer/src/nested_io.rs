//! SA-1905 — the nested Arrow file: writing it, and reading it back to Python.
//!
//! # File layout
//!
//! The root shape decides the batch schema, mirroring the CSV model exactly so
//! the control plane's `value_kind` / `item_kind` rules keep working unchanged:
//!
//! * root is a `Struct` → the batch columns ARE its fields (the rows are the
//!   columns), i.e. what `dict_to_csv` produces;
//! * anything else → one column named `__value`, i.e. what `values_to_csv`
//!   produces for a scalar / list-of-scalar root.
//!
//! So a reader always gets **a list of dicts**, and `_unwrap_value` — gated on
//! the ref's own declaration by `value_kind.ref_uses_value_column` — decides
//! whether to unwrap, exactly as it does for CSV. No second rule, no sniffing.
//!
//! # Why the reader lives here and not in pyarrow
//!
//! `pyarrow` would be a 50-100 MB runtime dependency on a control plane pinned
//! at `instances: 1`. All eleven images already compile this crate, so building
//! the Python objects in Rust costs a wheel that is already being built.
//! `ArrowFileStream.__arrow_c_stream__` stays for external consumers who do
//! have pyarrow (duckdb, polars, datafusion).
//!
//! # What `strict_parity` still has to do
//!
//! Much less than the plan assumed, because the writer was fixed rather than
//! the reader: `Int64` reads back as a Python `int` and `_cast_value` also
//! returns `int`; `Float64` now only exists when every literal was fractional,
//! where `_cast_value` also returns `float`. The one remaining divergence is a
//! `token` column — a path holding more than one scalar kind — where CSV casts
//! each cell by the column's DECLARED type and gets a heterogeneous column back
//! (`[5, "N/A"]`), while the honest reading is the raw text (`["5", "N/A"]`).
//! `strict_parity` reproduces the former.

use std::collections::HashMap;
use std::fs::File;
use std::io::{BufWriter, Cursor};

use arrow::array::{
    Array, ArrayRef, AsArray, BooleanArray, Float64Array, Int64Array, ListArray, RecordBatch,
    RecordBatchOptions, StringArray, StructArray,
};
use arrow::datatypes::{Field, Fields, Schema as ArrowSchema, SchemaRef};
use arrow::ipc::reader::FileReader;
use arrow::ipc::writer::FileWriter;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use serde_json::Value;
use std::sync::Arc;

use super::nested::{ObservedNode, Shape};
use super::nested_arrow::{field_for, NodeBuilder, META_DECLARED, META_ENCODING};
use super::{open_lines, parse_line, K_BOOLEAN, K_NUMBER, VALUE_COLUMN};

/// Rows per batch — what sits in RAM, not the file.
const BATCH_ROWS: usize = 8192;

// ---------------------------------------------------------------------------
// Write
// ---------------------------------------------------------------------------

/// PASS 1, tree flavour: observe every line without flattening.
pub(crate) fn sketch_tree(path: &str) -> PyResult<(Shape, bool)> {
    let mut root = ObservedNode::new();
    for line in open_lines(path)? {
        let line = line.map_err(|e| PyValueError::new_err(format!("read error: {e}")))?;
        if line.trim().is_empty() {
            continue;
        }
        let v = parse_line(&line).map_err(PyValueError::new_err)?;
        root.observe(&v, 0);
    }
    Ok((root.resolve(), root.root_is_all_objects()))
}

/// `(schema, per-column shapes, struct_root)`.
fn root_schema(shape: &Shape, struct_root: bool) -> (SchemaRef, Vec<(String, Shape)>, bool) {
    match shape {
        Shape::Struct(fs) if struct_root => {
            let fields: Fields = fs
                .iter()
                .map(|(k, s)| field_for(k, s))
                .collect::<Vec<_>>()
                .into();
            (Arc::new(ArrowSchema::new(fields)), fs.clone(), true)
        }
        other => {
            let f = field_for(VALUE_COLUMN, other);
            (
                Arc::new(ArrowSchema::new(Fields::from(vec![f]))),
                vec![(VALUE_COLUMN.to_string(), other.clone())],
                false,
            )
        }
    }
}

/// PASS 2, tree flavour. Bounded RAM: one batch at a time.
pub(crate) fn write_nested_impl(
    in_path: &str,
    out_path: &str,
    shape: &Shape,
    is_struct_root: bool,
    codec: Option<&str>,
) -> PyResult<usize> {
    let (schema, cols, struct_root) = root_schema(shape, is_struct_root);
    let out = File::create(out_path)
        .map_err(|e| PyValueError::new_err(format!("cannot create {out_path}: {e}")))?;
    let mut w = FileWriter::try_new_with_options(
        BufWriter::new(out), &schema, crate::codec::writer_options(codec),
    )
    .map_err(|e| PyValueError::new_err(format!("arrow writer init failed: {e}")))?;

    let mut builders: Vec<NodeBuilder> = cols.iter().map(|(_, s)| NodeBuilder::new(s)).collect();
    let mut pending = 0usize;
    let mut rows = 0usize;

    let mut flush = |bs: &mut Vec<NodeBuilder>, n: &mut usize, w: &mut FileWriter<BufWriter<File>>| -> PyResult<()> {
        if *n == 0 {
            return Ok(());
        }
        let arrays: Vec<ArrayRef> = bs.iter_mut().map(|b| b.finish()).collect();
        // The row count is passed EXPLICITLY because a batch can legitimately
        // have zero columns: `[{}, {}]` is a list of two empty objects, and a
        // record with no fields cannot tell arrow how many rows it holds.
        //
        // The CSV writer has the same problem and the same answer — `dict_to_csv`
        // emits the row-id column for exactly this shape, after a version that
        // did not left the file holding ZERO rows while the ref declared
        // `row_count = len(data)`, which then sized a lazy LOOP from the
        // declaration and ran a phantom iteration.
        let opts = RecordBatchOptions::new().with_row_count(Some(*n));
        let batch = RecordBatch::try_new_with_options(schema.clone(), arrays, &opts)
            .map_err(|e| PyValueError::new_err(format!("arrow batch build failed: {e}")))?;
        *n = 0;
        w.write(&batch)
            .map_err(|e| PyValueError::new_err(format!("arrow batch write failed: {e}")))
    };

    for line in open_lines(in_path)? {
        let line = line.map_err(|e| PyValueError::new_err(format!("read error: {e}")))?;
        if line.trim().is_empty() {
            continue;
        }
        let v = parse_line(&line).map_err(PyValueError::new_err)?;
        if struct_root {
            let obj = v.as_object();
            for (i, (name, _)) in cols.iter().enumerate() {
                builders[i].append(obj.and_then(|m| m.get(name)));
            }
        } else {
            builders[0].append(Some(&v));
        }
        pending += 1;
        rows += 1;
        if pending >= BATCH_ROWS {
            flush(&mut builders, &mut pending, &mut w)?;
        }
    }
    flush(&mut builders, &mut pending, &mut w)?;
    w.finish()
        .map_err(|e| PyValueError::new_err(format!("arrow finish failed: {e}")))?;
    Ok(rows)
}

// ---------------------------------------------------------------------------
// Read
// ---------------------------------------------------------------------------

/// A Python `int` of arbitrary width, from its literal text. Python ints are
/// unbounded, so a value too wide for `i64` still round-trips exactly — which is
/// the whole reason `NumberToken` exists.
fn py_int_from_str<'py>(py: Python<'py>, s: &str) -> PyResult<Bound<'py, PyAny>> {
    py.import("builtins")?.getattr("int")?.call1((s,))
}

fn json_to_py<'py>(py: Python<'py>, v: &Value) -> PyResult<Bound<'py, PyAny>> {
    Ok(match v {
        Value::Null => py.None().into_bound(py),
        Value::Bool(b) => b.into_pyobject(py)?.to_owned().into_any(),
        Value::Number(n) => {
            let t = n.to_string();
            match t.parse::<i64>() {
                Ok(i) => i.into_pyobject(py)?.into_any(),
                Err(_) => {
                    if t.contains('.') || t.contains('e') || t.contains('E') {
                        t.parse::<f64>()
                            .map_err(|e| PyValueError::new_err(format!("bad number {t}: {e}")))?
                            .into_pyobject(py)?
                            .into_any()
                    } else {
                        py_int_from_str(py, &t)?
                    }
                }
            }
        }
        Value::String(s) => s.into_pyobject(py)?.into_any(),
        Value::Array(items) => {
            let out = PyList::empty(py);
            for it in items {
                out.append(json_to_py(py, it)?)?;
            }
            out.into_any()
        }
        Value::Object(m) => {
            let d = PyDict::new(py);
            for (k, val) in m {
                d.set_item(k, json_to_py(py, val)?)?;
            }
            d.into_any()
        }
    })
}

/// `_cast_value`'s behaviour for a `token` cell, reproduced from the leaf kind
/// the writer recorded. Deliberately in one place: the CSV reader's rule and
/// this one are the same rule, and two expressions of one rule is the defect
/// `value_kind.py` exists to remove.
fn cast_token<'py>(py: Python<'py>, text: &str, declared: Option<&str>) -> PyResult<Bound<'py, PyAny>> {
    match declared {
        Some(K_NUMBER) => {
            if let Ok(i) = text.parse::<i64>() {
                return Ok(i.into_pyobject(py)?.into_any());
            }
            if let Ok(f) = text.parse::<f64>() {
                return Ok(f.into_pyobject(py)?.into_any());
            }
            Ok(text.into_pyobject(py)?.into_any())
        }
        Some(K_BOOLEAN) => match text {
            "true" => Ok(true.into_pyobject(py)?.to_owned().into_any()),
            "false" => Ok(false.into_pyobject(py)?.to_owned().into_any()),
            other => Ok(other.into_pyobject(py)?.into_any()),
        },
        _ => Ok(text.into_pyobject(py)?.into_any()),
    }
}

fn meta<'a>(f: &'a Field, key: &str) -> Option<&'a str> {
    f.metadata().get(key).map(String::as_str)
}

/// One cell → one Python object.
fn cell_to_py<'py>(
    py: Python<'py>,
    arr: &ArrayRef,
    f: &Field,
    i: usize,
    strict: bool,
) -> PyResult<Bound<'py, PyAny>> {
    if arr.is_null(i) {
        return Ok(py.None().into_bound(py));
    }
    let enc = meta(f, META_ENCODING);
    // The writer's decision, read back — never re-sniffed from the bytes.
    match enc {
        Some("json") => {
            let s = arr.as_any().downcast_ref::<StringArray>().unwrap().value(i);
            let v: Value = serde_json::from_str(s)
                .map_err(|e| PyValueError::new_err(format!("bad json cell: {e}")))?;
            return json_to_py(py, &v);
        }
        Some("number_token") => {
            let s = arr.as_any().downcast_ref::<StringArray>().unwrap().value(i);
            return if s.contains('.') || s.contains('e') || s.contains('E') {
                Ok(s.parse::<f64>()
                    .map_err(|e| PyValueError::new_err(format!("bad number {s}: {e}")))?
                    .into_pyobject(py)?
                    .into_any())
            } else {
                py_int_from_str(py, s)
            };
        }
        Some("token") => {
            let s = arr.as_any().downcast_ref::<StringArray>().unwrap().value(i);
            return if strict {
                cast_token(py, s, meta(f, META_DECLARED))
            } else {
                Ok(s.into_pyobject(py)?.into_any())
            };
        }
        _ => {}
    }
    match arr.data_type() {
        arrow::datatypes::DataType::Null => Ok(py.None().into_bound(py)),
        arrow::datatypes::DataType::Boolean => Ok(arr
            .as_any()
            .downcast_ref::<BooleanArray>()
            .unwrap()
            .value(i)
            .into_pyobject(py)?
            .to_owned()
            .into_any()),
        arrow::datatypes::DataType::Int64 => Ok(arr
            .as_any()
            .downcast_ref::<Int64Array>()
            .unwrap()
            .value(i)
            .into_pyobject(py)?
            .into_any()),
        arrow::datatypes::DataType::Float64 => Ok(arr
            .as_any()
            .downcast_ref::<Float64Array>()
            .unwrap()
            .value(i)
            .into_pyobject(py)?
            .into_any()),
        arrow::datatypes::DataType::Utf8 => Ok(arr
            .as_any()
            .downcast_ref::<StringArray>()
            .unwrap()
            .value(i)
            .into_pyobject(py)?
            .into_any()),
        arrow::datatypes::DataType::Struct(fields) => {
            let sa = arr.as_any().downcast_ref::<StructArray>().unwrap();
            let d = PyDict::new(py);
            for (c, cf) in fields.iter().enumerate() {
                d.set_item(cf.name(), cell_to_py(py, sa.column(c), cf, i, strict)?)?;
            }
            Ok(d.into_any())
        }
        arrow::datatypes::DataType::List(item_field) => {
            let la = arr.as_any().downcast_ref::<ListArray>().unwrap();
            let vals = la.value(i);
            let out = PyList::empty(py);
            for j in 0..vals.len() {
                out.append(cell_to_py(py, &vals, item_field, j, strict)?)?;
            }
            Ok(out.into_any())
        }
        other => Err(PyValueError::new_err(format!(
            "unsupported arrow type {other:?}"
        ))),
    }
}

/// Read an Arrow IPC file **from bytes** into `list[dict]`.
///
/// Bytes-in rather than path-in on purpose: `IFileServiceClient.download_file`
/// hands the control plane `bytes`, and a path API would force a temp file into
/// the request path.
pub(crate) fn decode_batches(buf: Vec<u8>) -> PyResult<Vec<RecordBatch>> {
    let reader = FileReader::try_new(Cursor::new(buf), None)
        .map_err(|e| PyValueError::new_err(format!("not an arrow IPC file: {e}")))?;
    reader
        .map(|b| b.map_err(|e| PyValueError::new_err(format!("arrow read failed: {e}"))))
        .collect()
}

pub(crate) fn batches_to_py(
    py: Python<'_>,
    batches: &[RecordBatch],
    strict: bool,
) -> PyResult<PyObject> {
    let out = PyList::empty(py);
    for batch in batches {
        let schema = batch.schema();
        for i in 0..batch.num_rows() {
            let row = PyDict::new(py);
            for (c, f) in schema.fields().iter().enumerate() {
                row.set_item(f.name(), cell_to_py(py, batch.column(c), f, i, strict)?)?;
            }
            out.append(row)?;
        }
    }
    Ok(out.into_any().unbind())
}

pub(crate) fn read_nested_bytes(py: Python<'_>, buf: &[u8], strict: bool) -> PyResult<PyObject> {
    let batches = decode_batches(buf.to_vec())?;
    batches_to_py(py, &batches, strict)
}

// ---------------------------------------------------------------------------
// Python surface
// ---------------------------------------------------------------------------

/// PASS 1 + PASS 2, tree flavour. Returns `(row_count, column_types)`.
///
/// The GIL is released for the whole conversion, like every other producer in
/// this crate — a 561 MB payload once froze the entire interpreter for 39.7 s,
/// missing worker heartbeats and lease renewals along the way.
#[pyfunction]
#[pyo3(signature = (in_path, out_path, codec = None))]
pub fn jsonl_to_nested_arrow(
    py: Python<'_>,
    in_path: &str,
    out_path: &str,
    codec: Option<&str>,
) -> PyResult<(usize, HashMap<String, String>)> {
    let (in_path, out_path) = (in_path.to_owned(), out_path.to_owned());
    let codec = codec.map(str::to_owned);
    py.allow_threads(move || {
        let (shape, struct_root) = sketch_tree(&in_path)?;
        let types = declared_types(&shape, struct_root);
        let n = write_nested_impl(&in_path, &out_path, &shape, struct_root, codec.as_deref())?;
        Ok((n, types))
    })
}

/// One scan, two sketches, two writers: the CSV exactly as it is today, plus the
/// nested Arrow sidecar.
///
/// This is the worker's entry point. The CSV half is byte-for-byte what
/// `jsonl_to_csv` produces — asserted by
/// `the_csv_half_is_byte_identical_to_the_csv_only_path`, over the nested corpus
/// — because the steward edits that file and OData pages it. Buying a faster
/// read path with a quietly reshaped steward file would be a far worse trade
/// than the time it saves.
///
/// Returns the same 4-tuple as `jsonl_to_csv` so the caller's ref-building code
/// does not change, plus the nested column types.
#[pyfunction]
#[pyo3(signature = (in_path, csv_path, arrow_path, codec = None))]
pub fn jsonl_to_csv_and_nested_arrow(
    py: Python<'_>,
    in_path: &str,
    csv_path: &str,
    arrow_path: &str,
    codec: Option<&str>,
) -> PyResult<(Vec<String>, HashMap<String, String>, usize, String)> {
    let (in_path, csv_path, arrow_path) =
        (in_path.to_owned(), csv_path.to_owned(), arrow_path.to_owned());
    let codec = codec.map(str::to_owned);
    py.allow_threads(move || {
        let mut tree = ObservedNode::new();
        let schema = super::sketch_into(&in_path, Some(&mut tree))?;
        let shape = tree.resolve();
        let struct_root = tree.root_is_all_objects();
        let (columns, types, item_kind, value_mode, json_cells) = schema.finish();
        let n = super::write_csv_impl(&in_path, &csv_path, &columns, json_cells, value_mode)?;
        write_nested_impl(&in_path, &arrow_path, &shape, struct_root, codec.as_deref())?;
        Ok((columns, types, n, item_kind.to_string()))
    })
}

/// Read an Arrow IPC file from bytes into `list[dict]`.
///
/// The IPC decode runs WITHOUT the GIL and only the Python object construction
/// holds it. Building the objects is inherently GIL-bound — that is CPython, not
/// a choice — but the decode is not, and on a large file it is the larger half.
#[pyfunction]
#[pyo3(signature = (buf, strict_parity = true))]
pub fn arrow_bytes_to_rows(py: Python<'_>, buf: &[u8], strict_parity: bool) -> PyResult<PyObject> {
    let owned = buf.to_vec();
    let batches = py.allow_threads(move || decode_batches(owned))?;
    batches_to_py(py, &batches, strict_parity)
}

/// The `{column: type}` map the control plane records on the ref. Derived from
/// the ARROW schema so a nested file still declares what the CSV twin declares
/// for the same top-level paths.
pub(crate) fn declared_types(shape: &Shape, struct_root: bool) -> HashMap<String, String> {
    let (_, cols, _) = root_schema(shape, struct_root);
    let mut out = HashMap::new();
    for (name, s) in cols {
        let t = match s {
            Shape::Bool => "boolean",
            Shape::Int | Shape::Float | Shape::NumberToken => "number",
            Shape::Str => "string",
            Shape::Token(d) => d,
            Shape::Struct(_) | Shape::List(_) | Shape::Json => "json",
            Shape::Null => continue, // unlocked, like the CSV writer leaves it
        };
        out.insert(name, t.to_string());
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmpdir(tag: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("jcs-nio-{tag}"));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    /// `cargo test` links libpython but does not start it — the wheel never
    /// needs to, because CPython is already running when the module is
    /// imported. Idempotent, so every test can call it.
    fn py_ready() {
        pyo3::prepare_freethreaded_python();
    }

    /// Write the lines, read them back, and hand the result to the closure as
    /// a repr string — the round trip end to end, in one process.
    fn roundtrip(tag: &str, lines: &str, strict: bool) -> String {
        py_ready();
        let d = tmpdir(tag);
        let inp = d.join("in.jsonl");
        let outp = d.join("out.arrow");
        std::fs::write(&inp, lines).unwrap();
        let (shape, struct_root) = sketch_tree(inp.to_str().unwrap()).unwrap();
        write_nested_impl(
            inp.to_str().unwrap(), outp.to_str().unwrap(), &shape, struct_root, None,
        )
        .unwrap();
        let buf = std::fs::read(&outp).unwrap();
        let s = Python::with_gil(|py| {
            let v = read_nested_bytes(py, &buf, strict).unwrap();
            v.bind(py).repr().unwrap().to_string()
        });
        let _ = std::fs::remove_dir_all(&d);
        s
    }

    #[test]
    fn a_nested_payload_round_trips_with_no_join() {
        // the exact shape that is 3 CSV files + a __parent_row_id join today
        let got = roundtrip(
            "nested",
            "{\"orders\":[{\"id\":1,\"items\":[{\"sku\":\"A\"}]}]}\n",
            true,
        );
        assert_eq!(got, "[{'orders': [{'id': 1, 'items': [{'sku': 'A'}]}]}]");
    }

    #[test]
    fn a_non_object_root_lands_under_the_reserved_column() {
        assert_eq!(
            roundtrip("value", "1\n2\n3\n", true),
            "[{'__value': 1}, {'__value': 2}, {'__value': 3}]"
        );
    }

    /// SA-1988: an empty list, a null list and a missing key are three things.
    #[test]
    fn empty_null_and_missing_stay_distinct() {
        assert_eq!(
            roundtrip("enm", "{\"a\":[1]}\n{\"a\":[]}\n{\"a\":null}\n{}\n", true),
            "[{'a': [1]}, {'a': []}, {'a': None}, {'a': None}]"
        );
    }

    /// SA-1984, the half CSV loses outright.
    #[test]
    fn a_scalar_beside_a_structure_keeps_both() {
        assert_eq!(
            roundtrip("collide", "{\"x\":5}\n{\"x\":{\"a\":1}}\n", true),
            "[{'x': 5}, {'x': {'a': 1}}]"
        );
    }

    #[test]
    fn big_integers_survive_as_python_ints() {
        assert_eq!(
            roundtrip("big", "{\"id\":10000000000000000000000000}\n", true),
            "[{'id': 10000000000000000000000000}]"
        );
        assert_eq!(
            roundtrip("big53", "{\"id\":9007199254740993}\n", true),
            "[{'id': 9007199254740993}]"
        );
    }

    /// The int/float literal distinction `Float64` would have erased.
    #[test]
    fn integer_and_fractional_literals_stay_distinct() {
        assert_eq!(
            roundtrip("mixnum", "{\"n\":1}\n{\"n\":1.5}\n{\"n\":1.0}\n", true),
            "[{'n': 1}, {'n': 1.5}, {'n': 1.0}]"
        );
    }

    /// The only place `strict_parity` still changes anything.
    #[test]
    fn strict_parity_only_moves_token_columns() {
        let lines = "{\"n\":5}\n{\"n\":\"N/A\"}\n";
        assert_eq!(roundtrip("tok_s", lines, true), "[{'n': 5}, {'n': 'N/A'}]");
        assert_eq!(roundtrip("tok_l", lines, false), "[{'n': '5'}, {'n': 'N/A'}]");
    }

    #[test]
    fn a_leading_zero_string_is_never_a_number() {
        assert_eq!(
            roundtrip("lz", "{\"zip\":\"03230\"}\n", true),
            "[{'zip': '03230'}]"
        );
    }

    #[test]
    fn a_nested_file_ref_comes_back_whole() {
        let got = roundtrip(
            "fref",
            "{\"out\":{\"__file_ref\":true,\"file_id\":\"f1\"}}\n",
            true,
        );
        assert!(got.contains("'__file_ref': True"), "{got}");
        assert!(got.contains("'file_id': 'f1'"), "{got}");
    }

    /// The guarantee that lets the sidecar ship at all: adding it does not move
    /// one byte of the file a steward edits and OData pages.
    #[test]
    fn the_csv_half_is_byte_identical_to_the_csv_only_path() {
        py_ready();
        for (tag, lines) in [
            ("objects", "{\"a\":1,\"s\":\"x,y \\\"q\\\"\"}
{\"b\":2}
"),
            (
                "nested",
                "{\"orders\":[{\"id\":1,\"items\":[{\"sku\":\"A\"}]}]}
{\"orders\":[]}
",
            ),
            ("sparse", "{\"a\":[]}
{\"a\":[{\"x\":1}]}
{\"a\":null}
"),
            ("collide", "{\"x\":5}
{\"x\":{\"a\":1}}
"),
            ("values", "1
\"loose\"
null
"),
            ("scalars", "1
2
3
"),
            ("bigint", "{\"id\":9007199254740993}
{\"id\":7}
"),
            ("mixnum", "{\"n\":1}
{\"n\":1.5}
"),
        ] {
            let d = tmpdir(&format!("csvid-{tag}"));
            let inp = d.join("in.jsonl");
            std::fs::write(&inp, lines).unwrap();

            let only = d.join("only.csv");
            let (c1, t1, n1, k1) = Python::with_gil(|py| {
                super::super::jsonl_to_csv(py, inp.to_str().unwrap(), only.to_str().unwrap())
            })
            .unwrap();

            let both_csv = d.join("both.csv");
            let both_arrow = d.join("both.arrow");
            let (c2, t2, n2, k2) = Python::with_gil(|py| {
                jsonl_to_csv_and_nested_arrow(
                    py,
                    inp.to_str().unwrap(),
                    both_csv.to_str().unwrap(),
                    both_arrow.to_str().unwrap(),
                    None,
                )
            })
            .unwrap();

            assert_eq!(
                std::fs::read(&only).unwrap(),
                std::fs::read(&both_csv).unwrap(),
                "{tag}: CSV bytes moved"
            );
            assert_eq!((c1, t1, n1, k1), (c2, t2, n2, k2), "{tag}: metadata moved");
            assert!(std::fs::metadata(&both_arrow).unwrap().len() > 0, "{tag}: no arrow");
            let _ = std::fs::remove_dir_all(&d);
        }
    }

    /// The root's value-mode decision must mirror `_is_value_root`. A null
    /// LINE is a value, not an empty record: a RecordBatch has no row-level
    /// validity, so a struct root would turn `[{...}, null, {...}]` into
    /// `[{...}, {"id": null}, {...}]` — and the CSV path gets this right, so
    /// getting it wrong here would be a sidecar that is worse than no sidecar.
    #[test]
    fn a_null_line_forces_the_reserved_column() {
        assert_eq!(
            roundtrip("nullline", "{\"id\":1}\nnull\n{\"id\":3}\n", true),
            "[{'__value': {'id': 1}}, {'__value': None}, {'__value': {'id': 3}}]"
        );
    }

    /// A list of EMPTY objects. A record with no fields cannot tell arrow how
    /// many rows it has, so the count must be passed explicitly — the same
    /// shape, and the same fix, as `dict_to_csv`'s row-id column.
    #[test]
    fn a_list_of_empty_objects_keeps_its_row_count() {
        assert_eq!(roundtrip("emptyobj", "{}
{}
{}
", true), "[{}, {}, {}]");
    }

    #[test]
    fn more_rows_than_one_batch() {
        py_ready();
        let mut lines = String::new();
        for i in 0..(BATCH_ROWS + 3) {
            lines.push_str(&format!("{{\"i\":{i}}}\n"));
        }
        let d = tmpdir("batched");
        let inp = d.join("in.jsonl");
        let outp = d.join("out.arrow");
        std::fs::write(&inp, &lines).unwrap();
        let (shape, struct_root) = sketch_tree(inp.to_str().unwrap()).unwrap();
        let n = write_nested_impl(
            inp.to_str().unwrap(), outp.to_str().unwrap(), &shape, struct_root, None,
        )
        .unwrap();
        assert_eq!(n, BATCH_ROWS + 3);
        let buf = std::fs::read(&outp).unwrap();
        Python::with_gil(|py| {
            let v = read_nested_bytes(py, &buf, true).unwrap();
            let l = v.bind(py).downcast::<PyList>().unwrap().len();
            assert_eq!(l, BATCH_ROWS + 3);
        });
        let _ = std::fs::remove_dir_all(&d);
    }
}
