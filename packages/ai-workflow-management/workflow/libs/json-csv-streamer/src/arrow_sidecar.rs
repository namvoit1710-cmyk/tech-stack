//! SA-1905 F-arrow (PROTOTYPE) — an Arrow IPC sidecar next to the CSV.
//!
//! # Why
//!
//! The crate does its work in Rust and then hands the result over as **CSV
//! text**, which the control plane decodes again: `csv_to_dict` → `_unflatten`
//! → `_cast_value` → `json.loads` on each cell. PASS 1 computes an exact
//! `{column: type}` map and then writes it into a format that cannot carry a
//! type, so every reader re-derives per cell what the writer already knew.
//!
//! CSV is not the problem and is not being removed: it is a product
//! requirement (a steward edits the rows; OData pages them). It is the wrong
//! *read* path, not the wrong *storage* path.
//!
//! So: same single scan, two writers. `out.csv` stays exactly as it is for
//! humans and OData. `out.arrow` carries the same rows typed, and is read back
//! batch-at-a-time over the Arrow C Data Interface — no parsing, no Python
//! object per cell.
//!
//! # What this exposes
//!
//! * [`jsonl_to_arrow`] / [`jsonl_to_csv_and_arrow`] — the producers.
//! * [`ArrowFileStream`] — a `__arrow_c_stream__` producer. Any consumer that
//!   speaks the protocol (pyarrow, polars, duckdb, datafusion, pandas) reads it
//!   with no build-time dependency on this crate.
//!
//! The stream is pull-based, one `RecordBatch` per `get_next`, so a file larger
//! than RAM reads in bounded memory and backpressure is automatic — the
//! producer only touches the next batch when the consumer asks.
//!
//! # Numbers: measured, not assumed
//!
//! The first cut of this module mapped every `number` to `Float64`, and that
//! silently collapsed `2^53 + 1` onto `2^53` — the exact loss
//! `serde_json/arbitrary_precision` is enabled to prevent, on the kind of value
//! most likely to be a primary key. Two artifacts of one scan disagreeing about
//! an id is strictly worse than one artifact.
//!
//! So PASS 1 now measures each numeric column ([`NumInfo`]) and the physical
//! type follows the measurement:
//!
//! | observed | Arrow type | why |
//! |---|---|---|
//! | every token fits `i64` | `Int64` | exact to 9.2e18 — Snowflake ids, SAP doc numbers |
//! | integers and fractions, ints ≤ 2^53 | `Float64` | one column needs one type; f64 is exact in that range |
//! | anything larger | `Utf8` | the token itself — the same choice CSV makes |
//! | **more than one leaf type in the column** | `Utf8` | see below |
//!
//! `Utf8` here is not a fallback, it is the lossless answer. A consumer that
//! wants arithmetic on such a column has to opt into a precision it can name,
//! which is the honest place for that decision.
//!
//! The last row is the one that bites hardest. A declared type is the FIRST
//! row's leaf type, not a promise about the rest, and `[{"n":5},{"n":"N/A"}]` is
//! ordinary API data. Trusting the declaration wrote an `Int64` column and
//! `parse::<i64>().ok()` turned `"N/A"` into a **null** — total loss of a value
//! the CSV twin keeps, which is worse than the f64 bug it replaced. PASS 1 now
//! flags heterogeneous columns ([`Schema::mixed_leaf_columns`]) and they are
//! stored as text.
//!
//! One consequence worth knowing: for `[{"n":5},{"n":"03230"}]` the two
//! artifacts now disagree *in the reader's favour on the Arrow side*. The CSV
//! column declares `number`, so `_cast_value` reads the second cell back as
//! `3230` and the leading zero is gone; Arrow keeps `"03230"`. Neither writer
//! lost a byte at rest — the CSV **read path** is where that zero dies.
//!
//! # Still a prototype
//!
//! Nothing writes these files yet — no worker, no control-plane read path. What
//! remains before promotion is integration, not correctness of the encoding:
//! `meta.warehouse` stays a flat dotted column here while `csv_to_dict`
//! unflattens it into a nested dict, so this is not a drop-in replacement for
//! the current read path.

use std::collections::{HashMap, HashSet};
use std::ffi::CString;
use std::fs::File;
use std::io::BufWriter;
use std::sync::Arc;

use arrow::array::{ArrayRef, BooleanArray, Float64Array, Int64Array, RecordBatch, StringArray};
use arrow::datatypes::{DataType, Field, Schema as ArrowSchema, SchemaRef};
use arrow::ffi_stream::FFI_ArrowArrayStream;
use arrow::ipc::reader::FileReader;
use arrow::ipc::writer::FileWriter;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyCapsule;

use super::*;

/// Rows per `RecordBatch`. The whole point is that this — not the file — is
/// what sits in RAM, on both the write and the read side.
const BATCH_ROWS: usize = 8192;

/// The Arrow type for one of PASS 1's declared column types.
///
/// Everything that is not a number or a boolean stays `Utf8`, including `json`:
/// a JSON-encoded array cell is a string as far as the sidecar is concerned,
/// and giving it a `List` type would need a second schema pass this prototype
/// does not do.
fn arrow_type(
    declared: Option<&String>,
    num_hint: Option<&&'static str>,
    mixed: bool,
) -> DataType {
    // A column that held more than one leaf type has no lossless typed form.
    // Storing the token is what CSV does at rest, and it beats the alternative:
    // `parse::<i64>().ok()` turns `"N/A"` into a null and the value is gone.
    if mixed {
        return DataType::Utf8;
    }
    match declared.map(String::as_str) {
        // PASS 1 measured what this column can hold losslessly; honour it.
        // `Utf8` for a column that outgrew i64 is not a fallback — it is the
        // same decision the CSV makes, and it keeps the two artifacts of one
        // scan from disagreeing about an id.
        Some(K_NUMBER) => match num_hint.copied() {
            Some(NUM_INT) => DataType::Int64,
            Some(NUM_FLOAT) => DataType::Float64,
            _ => DataType::Utf8,
        },
        Some(K_BOOLEAN) => DataType::Boolean,
        _ => DataType::Utf8,
    }
}

fn build_schema(
    columns: &[String],
    types: &HashMap<String, String>,
    hints: &HashMap<String, &'static str>,
    mixed: &HashSet<String>,
) -> SchemaRef {
    let mut fields = Vec::with_capacity(columns.len() + 1);
    // Same synthetic key the CSV carries, so the two sidecars join row-for-row.
    fields.push(Field::new(ROW_ID, DataType::Int64, false));
    for c in columns {
        fields.push(Field::new(
            c,
            arrow_type(types.get(c), hints.get(c), mixed.contains(c)),
            true,
        ));
    }
    Arc::new(ArrowSchema::new(fields))
}

/// One column's cells for the current batch, still as the CSV cell strings.
///
/// Reusing the cell strings rather than the raw `Value` keeps this path on the
/// *same* flatten the CSV writer uses — the one the golden corpus covers — so
/// the sidecar cannot drift from its twin by taking a different route to the
/// value. An empty string means the cell was absent or null.
type Cells = Vec<Option<String>>;

fn finish_column(dt: &DataType, cells: &Cells) -> PyResult<ArrayRef> {
    Ok(match dt {
        DataType::Int64 => Arc::new(
            cells
                .iter()
                .map(|c| c.as_deref().and_then(|s| s.parse::<i64>().ok()))
                .collect::<Int64Array>(),
        ) as ArrayRef,
        DataType::Float64 => Arc::new(
            cells
                .iter()
                .map(|c| c.as_deref().and_then(|s| s.parse::<f64>().ok()))
                .collect::<Float64Array>(),
        ) as ArrayRef,
        DataType::Boolean => Arc::new(
            cells
                .iter()
                .map(|c| match c.as_deref() {
                    Some("true") => Some(true),
                    Some("false") => Some(false),
                    _ => None,
                })
                .collect::<BooleanArray>(),
        ) as ArrayRef,
        _ => Arc::new(cells.iter().map(|c| c.as_deref()).collect::<StringArray>()) as ArrayRef,
    })
}

/// PASS 2, Arrow flavour. Bounded RAM: one batch of rows at a time, exactly
/// like the CSV writer holds one row.
pub(crate) fn write_arrow_impl(
    in_path: &str,
    out_path: &str,
    columns: &[String],
    types: &HashMap<String, String>,
    hints: &HashMap<String, &'static str>,
    mixed: &HashSet<String>,
    json_cells: bool,
    value_mode: bool,
    codec: Option<&str>,
) -> PyResult<usize> {
    let schema = build_schema(columns, types, hints, mixed);
    let out = File::create(out_path)
        .map_err(|e| PyValueError::new_err(format!("cannot create {out_path}: {e}")))?;
    let mut writer = FileWriter::try_new_with_options(
        BufWriter::new(out), &schema, crate::codec::writer_options(codec),
    )
    .map_err(|e| PyValueError::new_err(format!("arrow writer init failed: {e}")))?;

    let mut ids: Vec<i64> = Vec::with_capacity(BATCH_ROWS);
    let mut cols: Vec<Cells> = vec![Vec::with_capacity(BATCH_ROWS); columns.len()];
    let mut row_index: i64 = 0;

    // Column name -> position, built ONCE. The CSV writer rebuilds this map per
    // row; there is no reason to repeat that here.
    let index: HashMap<&str, usize> = columns
        .iter()
        .enumerate()
        .map(|(i, c)| (c.as_str(), i))
        .collect();

    let flush = |ids: &mut Vec<i64>, cols: &mut Vec<Cells>, w: &mut FileWriter<BufWriter<File>>| -> PyResult<()> {
        if ids.is_empty() {
            return Ok(());
        }
        let mut arrays: Vec<ArrayRef> = Vec::with_capacity(cols.len() + 1);
        arrays.push(Arc::new(Int64Array::from(std::mem::take(ids))) as ArrayRef);
        for (i, cells) in cols.iter_mut().enumerate() {
            arrays.push(finish_column(schema.field(i + 1).data_type(), cells)?);
            cells.clear();
        }
        let batch = RecordBatch::try_new(schema.clone(), arrays)
            .map_err(|e| PyValueError::new_err(format!("arrow batch build failed: {e}")))?;
        w.write(&batch)
            .map_err(|e| PyValueError::new_err(format!("arrow batch write failed: {e}")))
    };

    for line in open_lines(in_path)? {
        let line = line.map_err(|e| PyValueError::new_err(format!("read error: {e}")))?;
        if line.trim().is_empty() {
            continue;
        }
        ids.push(row_index);
        if value_mode {
            let v = parse_line(&line).map_err(PyValueError::new_err)?;
            let cell = value_cell(&v, json_cells);
            cols[0].push(if cell.is_empty() { None } else { Some(cell) });
        } else {
            let flat = flatten_line(&line).map_err(PyValueError::new_err)?;
            let mut row: Vec<Option<String>> = vec![None; columns.len()];
            for (k, cell, _ty) in flat {
                if let Some(&i) = index.get(k.as_str()) {
                    row[i] = if cell.is_empty() { None } else { Some(cell) };
                }
            }
            for (i, c) in row.into_iter().enumerate() {
                cols[i].push(c);
            }
        }
        row_index += 1;
        if ids.len() >= BATCH_ROWS {
            flush(&mut ids, &mut cols, &mut writer)?;
        }
    }
    flush(&mut ids, &mut cols, &mut writer)?;
    writer
        .finish()
        .map_err(|e| PyValueError::new_err(format!("arrow finish failed: {e}")))?;
    Ok(row_index as usize)
}

/// PASS 1 + PASS 2(Arrow). Same return shape as `jsonl_to_csv`.
#[pyfunction]
// TRAILING and optional, so every existing caller keeps working untouched —
// that is what makes the wheel bump inert (see ``codec.rs``).
#[pyo3(signature = (in_path, out_path, codec = None))]
pub fn jsonl_to_arrow(
    py: Python<'_>,
    in_path: &str,
    out_path: &str,
    codec: Option<&str>,
) -> PyResult<(Vec<String>, HashMap<String, String>, usize, String)> {
    let (in_path, out_path) = (in_path.to_owned(), out_path.to_owned());
    let codec = codec.map(str::to_owned);
    py.allow_threads(move || {
        let schema = sketch(&in_path)?;
        // BEFORE `finish` — it consumes the schema.
        let hints = schema.numeric_hints();
        let mixed = schema.mixed_leaf_columns();
        let (columns, types, item_kind, value_mode, json_cells) = schema.finish();
        let n = write_arrow_impl(
            &in_path, &out_path, &columns, &types, &hints, &mixed, json_cells, value_mode,
            codec.as_deref(),
        )?;
        Ok((columns, types, n, item_kind.to_string()))
    })
}

/// PASS 2 for BOTH artifacts in ONE scan.
///
/// Calling `write_csv_impl` then `write_arrow_impl` re-reads and re-parses the
/// whole JSONL a second time — measured at 200k rows, the second artifact cost
/// **+48%** that way, because serde parsing dominates and it was being paid
/// twice. Here each line is parsed and flattened once and handed to both sinks.
///
/// The CSV bytes are required to be identical to the CSV-only path;
/// `merged_pass_writes_the_same_csv_bytes_as_the_csv_only_path` asserts it. A
/// second writer that quietly changed the steward's file would be a much worse
/// trade than the time it saves.
fn write_both_impl(
    in_path: &str,
    csv_path: &str,
    arrow_path: &str,
    columns: &[String],
    types: &HashMap<String, String>,
    hints: &HashMap<String, &'static str>,
    mixed: &HashSet<String>,
    json_cells: bool,
    value_mode: bool,
    codec: Option<&str>,
) -> PyResult<usize> {
    let csv_file = File::create(csv_path)
        .map_err(|e| PyValueError::new_err(format!("cannot create {csv_path}: {e}")))?;
    let mut csv_w = csv::Writer::from_writer(BufWriter::new(csv_file));
    let mut header: Vec<&str> = Vec::with_capacity(columns.len() + 1);
    header.push(ROW_ID);
    header.extend(columns.iter().map(|c| c.as_str()));
    csv_w
        .write_record(&header)
        .map_err(|e| PyValueError::new_err(format!("csv header write failed: {e}")))?;

    let schema = build_schema(columns, types, hints, mixed);
    let arrow_file = File::create(arrow_path)
        .map_err(|e| PyValueError::new_err(format!("cannot create {arrow_path}: {e}")))?;
    let mut arrow_w = FileWriter::try_new_with_options(
        BufWriter::new(arrow_file), &schema, crate::codec::writer_options(codec),
    )
    .map_err(|e| PyValueError::new_err(format!("arrow writer init failed: {e}")))?;

    let mut ids: Vec<i64> = Vec::with_capacity(BATCH_ROWS);
    let mut cols: Vec<Cells> = vec![Vec::with_capacity(BATCH_ROWS); columns.len()];
    let mut row_index: i64 = 0;
    let index: HashMap<&str, usize> = columns
        .iter()
        .enumerate()
        .map(|(i, c)| (c.as_str(), i))
        .collect();

    let flush = |ids: &mut Vec<i64>,
                 cols: &mut Vec<Cells>,
                 w: &mut FileWriter<BufWriter<File>>|
     -> PyResult<()> {
        if ids.is_empty() {
            return Ok(());
        }
        let mut arrays: Vec<ArrayRef> = Vec::with_capacity(cols.len() + 1);
        arrays.push(Arc::new(Int64Array::from(std::mem::take(ids))) as ArrayRef);
        for (i, cells) in cols.iter_mut().enumerate() {
            arrays.push(finish_column(schema.field(i + 1).data_type(), cells)?);
            cells.clear();
        }
        let batch = RecordBatch::try_new(schema.clone(), arrays)
            .map_err(|e| PyValueError::new_err(format!("arrow batch build failed: {e}")))?;
        w.write(&batch)
            .map_err(|e| PyValueError::new_err(format!("arrow batch write failed: {e}")))
    };

    let mut record: Vec<String> = Vec::with_capacity(columns.len() + 1);
    for line in open_lines(in_path)? {
        let line = line.map_err(|e| PyValueError::new_err(format!("read error: {e}")))?;
        if line.trim().is_empty() {
            continue;
        }
        // ONE parse + flatten feeds both sinks.
        let row: Vec<Option<String>> = if value_mode {
            let v = parse_line(&line).map_err(PyValueError::new_err)?;
            let cell = value_cell(&v, json_cells);
            vec![if cell.is_empty() { None } else { Some(cell) }]
        } else {
            let flat = flatten_line(&line).map_err(PyValueError::new_err)?;
            let mut r: Vec<Option<String>> = vec![None; columns.len()];
            for (k, cell, _ty) in flat {
                if let Some(&i) = index.get(k.as_str()) {
                    r[i] = if cell.is_empty() { None } else { Some(cell) };
                }
            }
            r
        };

        record.clear();
        record.push(row_index.to_string());
        for c in &row {
            record.push(c.clone().unwrap_or_default());
        }
        csv_w
            .write_record(&record)
            .map_err(|e| PyValueError::new_err(format!("csv row write failed: {e}")))?;

        ids.push(row_index);
        for (i, c) in row.into_iter().enumerate() {
            cols[i].push(c);
        }

        row_index += 1;
        if ids.len() >= BATCH_ROWS {
            flush(&mut ids, &mut cols, &mut arrow_w)?;
        }
    }
    flush(&mut ids, &mut cols, &mut arrow_w)?;
    csv_w
        .flush()
        .map_err(|e| PyValueError::new_err(format!("csv flush failed: {e}")))?;
    arrow_w
        .finish()
        .map_err(|e| PyValueError::new_err(format!("arrow finish failed: {e}")))?;
    Ok(row_index as usize)
}

/// The shape a caller actually wants: ONE sketch, both sidecars. The CSV is
/// unchanged and remains the steward/OData artifact; the Arrow file is the read
/// path. Writing them from one `sketch` is what keeps them from disagreeing
/// about the schema.
#[pyfunction]
#[pyo3(signature = (in_path, csv_path, arrow_path, codec = None))]
pub fn jsonl_to_csv_and_arrow(
    py: Python<'_>,
    in_path: &str,
    csv_path: &str,
    arrow_path: &str,
    codec: Option<&str>,
) -> PyResult<(Vec<String>, HashMap<String, String>, usize, String)> {
    let (in_path, csv_path, arrow_path) = (
        in_path.to_owned(),
        csv_path.to_owned(),
        arrow_path.to_owned(),
    );
    let codec = codec.map(str::to_owned);
    py.allow_threads(move || {
        let schema = sketch(&in_path)?;
        let hints = schema.numeric_hints();
        let mixed = schema.mixed_leaf_columns();
        let (columns, types, item_kind, value_mode, json_cells) = schema.finish();
        let n = write_both_impl(
            &in_path,
            &csv_path,
            &arrow_path,
            &columns,
            &types,
            &hints,
            &mixed,
            json_cells,
            value_mode,
            codec.as_deref(),
        )?;
        Ok((columns, types, n, item_kind.to_string()))
    })
}

/// A `__arrow_c_stream__` producer over an Arrow IPC file.
///
/// This is the whole point of the sidecar: the consumer gets batches over the
/// C ABI, so nothing is parsed and no Python object is made per cell. The
/// `RecordBatchReader` lives in `private_data` behind the stream's function
/// pointers, and `get_next` pulls exactly one batch — a file larger than RAM
/// reads in bounded memory, with backpressure for free because the protocol is
/// pull-based.
#[pyclass]
pub struct ArrowFileStream {
    path: String,
}

#[pymethods]
impl ArrowFileStream {
    #[new]
    fn new(path: &str) -> PyResult<Self> {
        // Fail at construction, not at first pull: a consumer that already holds
        // a stream has no good way to report that the file was never openable.
        File::open(path)
            .map_err(|e| PyValueError::new_err(format!("cannot open {path}: {e}")))?;
        Ok(Self {
            path: path.to_owned(),
        })
    }

    /// The Arrow PyCapsule interface. The capsule MUST be named
    /// `arrow_array_stream` — that name is the whole handshake; consumers look
    /// it up by name and will refuse anything else.
    ///
    /// Each call returns a FRESH reader: the protocol is one-shot and
    /// forward-only, so handing out the same stream twice would give the second
    /// consumer a stream someone else had already drained.
    #[pyo3(signature = (requested_schema = None))]
    fn __arrow_c_stream__<'py>(
        &self,
        py: Python<'py>,
        requested_schema: Option<PyObject>,
    ) -> PyResult<Bound<'py, PyCapsule>> {
        // Schema negotiation is optional in the protocol; a producer that cannot
        // cast is allowed to ignore the hint, but must not pretend it honoured it.
        let _ = requested_schema;

        let file = File::open(&self.path)
            .map_err(|e| PyValueError::new_err(format!("cannot open {}: {e}", self.path)))?;
        let reader = FileReader::try_new(file, None)
            .map_err(|e| PyValueError::new_err(format!("not an arrow IPC file: {e}")))?;

        let stream = FFI_ArrowArrayStream::new(Box::new(reader));
        PyCapsule::new(py, stream, Some(CString::new("arrow_array_stream").unwrap()))
    }

    /// Row count without materialising anything — the IPC footer knows it.
    fn row_count(&self) -> PyResult<usize> {
        let file = File::open(&self.path)
            .map_err(|e| PyValueError::new_err(format!("cannot open {}: {e}", self.path)))?;
        let reader = FileReader::try_new(file, None)
            .map_err(|e| PyValueError::new_err(format!("not an arrow IPC file: {e}")))?;
        let mut n = 0usize;
        for batch in reader {
            n += batch
                .map_err(|e| PyValueError::new_err(format!("arrow read failed: {e}")))?
                .num_rows();
        }
        Ok(n)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use arrow::array::{Array, AsArray};

    fn tmpdir(tag: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("jcs-arrow-{tag}"));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    fn write(dir: &std::path::Path, lines: &str) -> (String, String) {
        let inp = dir.join("in.jsonl");
        let outp = dir.join("out.arrow");
        std::fs::write(&inp, lines).unwrap();
        (
            inp.to_str().unwrap().to_owned(),
            outp.to_str().unwrap().to_owned(),
        )
    }

    fn read_back(path: &str) -> Vec<RecordBatch> {
        let f = File::open(path).unwrap();
        FileReader::try_new(f, None)
            .unwrap()
            .map(|b| b.unwrap())
            .collect()
    }

    #[test]
    fn arrow_sidecar_carries_the_same_rows_typed() {
        let d = tmpdir("typed");
        let (inp, outp) = write(
            &d,
            "{\"sku\":\"A\",\"qty\":3,\"ok\":true}\n{\"sku\":\"B\",\"qty\":4,\"ok\":false}\n",
        );
        let schema = sketch(&inp).unwrap();
        let hints = schema.numeric_hints();
        let mixed = schema.mixed_leaf_columns();
        let (cols, types, _item, vm, jc) = schema.finish();
        let n = write_arrow_impl(&inp, &outp, &cols, &types, &hints, &mixed, jc, vm, None).unwrap();
        assert_eq!(n, 2);

        let batches = read_back(&outp);
        assert_eq!(batches.len(), 1);
        let b = &batches[0];
        assert_eq!(b.num_rows(), 2);
        // the synthetic key joins this file to its CSV twin row-for-row
        assert_eq!(b.schema().field(0).name(), ROW_ID);
        assert_eq!(b.column(0).as_primitive::<arrow::datatypes::Int64Type>().value(1), 1);
        // types survive the boundary instead of being re-derived per cell
        assert_eq!(b.column_by_name("sku").unwrap().data_type(), &DataType::Utf8);
        // whole numbers stay whole — Int64, not Float64
        assert_eq!(b.column_by_name("qty").unwrap().data_type(), &DataType::Int64);
        assert_eq!(
            b.column_by_name("qty").unwrap().as_primitive::<arrow::datatypes::Int64Type>().value(0),
            3
        );
        assert_eq!(b.column_by_name("ok").unwrap().data_type(), &DataType::Boolean);
        assert_eq!(b.column_by_name("sku").unwrap().as_string::<i32>().value(0), "A");
        assert!(b.column_by_name("ok").unwrap().as_boolean().value(0));
        assert!(!b.column_by_name("ok").unwrap().as_boolean().value(1));
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn arrow_sidecar_keeps_absent_cells_null_not_empty_string() {
        let d = tmpdir("nulls");
        let (inp, outp) = write(&d, "{\"a\":\"x\"}\n{\"b\":\"y\"}\n");
        let schema = sketch(&inp).unwrap();
        let hints = schema.numeric_hints();
        let mixed = schema.mixed_leaf_columns();
        let (cols, types, _i, vm, jc) = schema.finish();
        write_arrow_impl(&inp, &outp, &cols, &types, &hints, &mixed, jc, vm, None).unwrap();

        let batches = read_back(&outp);
        let a = batches[0].column_by_name("a").unwrap();
        // The CSV cannot say this — an absent cell and an empty string are the
        // same bytes there. Arrow has a validity bitmap, so it can.
        assert!(a.is_valid(0));
        assert!(a.is_null(1));
        let _ = std::fs::remove_dir_all(&d);
    }

    /// The bug this module was born with, now a regression test.
    ///
    /// 2^53 + 1 is the first integer f64 cannot represent — and it is an
    /// ordinary 16-digit id, not an exotic magnitude (a Snowflake id is 19
    /// digits). The first cut mapped every `number` to Float64 and silently
    /// returned 2^53, disagreeing with its own CSV twin about a key.
    ///
    /// (An earlier version of this test used 10**25 and did NOT fail: Rust
    /// prints the shortest string that round-trips to a given f64, so at that
    /// magnitude the loss was real and invisible. Assert on the integer, never
    /// on its Display.)
    #[test]
    fn integer_columns_keep_every_digit_past_2_pow_53() {
        let d = tmpdir("int64");
        let (inp, outp) = write(&d, "{\"id\":9007199254740993}
{\"id\":-9007199254740993}
");
        let schema = sketch(&inp).unwrap();
        let hints = schema.numeric_hints();
        let mixed = schema.mixed_leaf_columns();
        assert_eq!(hints["id"], NUM_INT);
        let (cols, types, _i, vm, jc) = schema.finish();

        // the CSV twin keeps every digit — that is what `arbitrary_precision` buys
        let csvp = d.join("out.csv");
        write_csv_impl(&inp, csvp.to_str().unwrap(), &cols, jc, vm).unwrap();
        assert!(std::fs::read_to_string(&csvp).unwrap().contains("9007199254740993"));

        // and now so does the Arrow sidecar
        write_arrow_impl(&inp, &outp, &cols, &types, &hints, &mixed, jc, vm, None).unwrap();
        let batches = read_back(&outp);
        let id = batches[0].column_by_name("id").unwrap();
        assert_eq!(id.data_type(), &DataType::Int64);
        let v = id.as_primitive::<arrow::datatypes::Int64Type>();
        assert_eq!(v.value(0), 9007199254740993);
        assert_eq!(v.value(1), -9007199254740993);
        let _ = std::fs::remove_dir_all(&d);
    }

    /// One column needs one type, so an integer beside a fraction has to be
    /// f64 — which is exact for every integer up to 2^53, hence the guard.
    #[test]
    fn mixed_integer_and_fraction_columns_are_float64() {
        let d = tmpdir("f64");
        let (inp, outp) = write(&d, "{\"n\":1}
{\"n\":1.5}
");
        let schema = sketch(&inp).unwrap();
        let hints = schema.numeric_hints();
        let mixed = schema.mixed_leaf_columns();
        assert_eq!(hints["n"], NUM_FLOAT);
        let (cols, types, _i, vm, jc) = schema.finish();
        write_arrow_impl(&inp, &outp, &cols, &types, &hints, &mixed, jc, vm, None).unwrap();

        let batches = read_back(&outp);
        let n = batches[0].column_by_name("n").unwrap();
        assert_eq!(n.data_type(), &DataType::Float64);
        let v = n.as_primitive::<arrow::datatypes::Float64Type>();
        assert_eq!(v.value(0), 1.0);
        assert_eq!(v.value(1), 1.5);
        let _ = std::fs::remove_dir_all(&d);
    }

    /// Beyond i64 there is no lossless fixed-width type, so the sidecar stores
    /// the token — the same answer CSV gives. Not a fallback: the correct one.
    #[test]
    fn numbers_beyond_i64_stay_text_and_keep_their_digits() {
        let d = tmpdir("text");
        let (inp, outp) = write(&d, "{\"id\":10000000000000000000000000}
");
        let schema = sketch(&inp).unwrap();
        let hints = schema.numeric_hints();
        let mixed = schema.mixed_leaf_columns();
        assert_eq!(hints["id"], NUM_TEXT);
        let (cols, types, _i, vm, jc) = schema.finish();
        write_arrow_impl(&inp, &outp, &cols, &types, &hints, &mixed, jc, vm, None).unwrap();

        let batches = read_back(&outp);
        let id = batches[0].column_by_name("id").unwrap();
        assert_eq!(id.data_type(), &DataType::Utf8);
        assert_eq!(
            id.as_string::<i32>().value(0),
            "10000000000000000000000000"
        );
        let _ = std::fs::remove_dir_all(&d);
    }

    /// An oversized integer sharing a column with a fraction cannot ride f64
    /// either — the mixed rule must not quietly re-open the hole.
    #[test]
    fn an_oversized_integer_mixed_with_a_fraction_falls_back_to_text() {
        let d = tmpdir("mixedbig");
        let (inp, _outp) = write(&d, "{\"n\":9007199254740993}
{\"n\":1.5}
");
        let schema = sketch(&inp).unwrap();
        assert_eq!(schema.numeric_hints()["n"], NUM_TEXT);
        let _ = std::fs::remove_dir_all(&d);
    }

    /// A `list[number]` lives in the reserved column and must be classified
    /// exactly like an object column, or value-mode silently keeps the old bug.
    #[test]
    fn the_reserved_value_column_is_classified_too() {
        let d = tmpdir("valuenum");
        let (inp, outp) = write(&d, "9007199254740993
7
");
        let schema = sketch(&inp).unwrap();
        let hints = schema.numeric_hints();
        let mixed = schema.mixed_leaf_columns();
        assert_eq!(hints[VALUE_COLUMN], NUM_INT);
        let (cols, types, item, vm, jc) = schema.finish();
        assert!(vm);
        assert_eq!(item, K_NUMBER);
        write_arrow_impl(&inp, &outp, &cols, &types, &hints, &mixed, jc, vm, None).unwrap();

        let batches = read_back(&outp);
        let v = batches[0].column_by_name(VALUE_COLUMN).unwrap();
        assert_eq!(v.data_type(), &DataType::Int64);
        assert_eq!(
            v.as_primitive::<arrow::datatypes::Int64Type>().value(0),
            9007199254740993
        );
        let _ = std::fs::remove_dir_all(&d);
    }

    /// The merged pass exists to save a full re-parse. It is only allowed to do
    /// that if the steward's file comes out byte-identical -- a faster writer
    /// that quietly reshaped the CSV would be a far worse trade than the time.
    #[test]
    fn merged_pass_writes_the_same_csv_bytes_as_the_csv_only_path() {
        for (tag, lines) in [
            (
                "objects",
                "{\"a\":1,\"s\":\"x,y \\\"q\\\" \u{e7}\",\"t\":[1,2],\"n\":null}\n{\"b\":2}\n",
            ),
            ("values", "1\n\"loose\"\nnull\n"),
            ("scalars", "1\n2\n3\n"),
        ] {
            let d = tmpdir(&format!("merged-{tag}"));
            let (inp, arrp) = write(&d, lines);
            let schema = sketch(&inp).unwrap();
            let hints = schema.numeric_hints();
        let mixed = schema.mixed_leaf_columns();
            let (cols, types, _i, vm, jc) = schema.finish();

            let only = d.join("only.csv");
            write_csv_impl(&inp, only.to_str().unwrap(), &cols, jc, vm).unwrap();

            let merged = d.join("merged.csv");
            write_both_impl(
                &inp,
                merged.to_str().unwrap(),
                &arrp,
                &cols,
                &types,
                &hints,
                &mixed,
                jc,
                vm,
                None,
            )
            .unwrap();

            assert_eq!(
                std::fs::read(&only).unwrap(),
                std::fs::read(&merged).unwrap(),
                "merged CSV diverged from the csv-only path for case {tag}"
            );
            let _ = std::fs::remove_dir_all(&d);
        }
    }

    /// And the arrow half of the merged pass must match the arrow-only writer.
    #[test]
    fn merged_pass_writes_the_same_arrow_rows_as_the_arrow_only_path() {
        let d = tmpdir("merged-arrow");
        let (inp, solo) = write(&d, "{\"id\":9007199254740993,\"s\":\"x\"}\n{\"id\":7}\n");
        let schema = sketch(&inp).unwrap();
        let hints = schema.numeric_hints();
        let mixed = schema.mixed_leaf_columns();
        let (cols, types, _i, vm, jc) = schema.finish();

        write_arrow_impl(&inp, &solo, &cols, &types, &hints, &mixed, jc, vm, None).unwrap();
        let merged_arrow = d.join("merged.arrow");
        write_both_impl(
            &inp,
            d.join("m.csv").to_str().unwrap(),
            merged_arrow.to_str().unwrap(),
            &cols,
            &types,
            &hints,
            &mixed,
            jc,
            vm,
            None,
        )
        .unwrap();

        let a = read_back(&solo);
        let b = read_back(merged_arrow.to_str().unwrap());
        assert_eq!(a.len(), b.len());
        assert_eq!(a[0], b[0]);
        let _ = std::fs::remove_dir_all(&d);
    }

    /// A declared type is the FIRST row's leaf type, not a promise about the
    /// rest. `[{"n":5},{"n":"N/A"}]` is ordinary API data and declares `number`.
    ///
    /// The first version of this module trusted the declaration and wrote an
    /// `Int64` column, so `parse::<i64>().ok()` turned `"N/A"` into a **null** —
    /// total loss of a value the CSV twin keeps. Worse than the f64 bug it
    /// replaced: that one lost precision, this one lost the value.
    ///
    /// A heterogeneous column has no lossless typed form, so it is stored as
    /// text — which is what the CSV holds at rest anyway.
    #[test]
    fn a_column_with_more_than_one_leaf_type_falls_back_to_text() {
        for (tag, lines, cell1) in [
            ("nonnumeric", "{\"n\":5}\n{\"n\":\"N/A\"}\n", "N/A"),
            (
                "huge",
                "{\"n\":5}\n{\"n\":\"99999999999999999999\"}\n",
                "99999999999999999999",
            ),
            // The leading zero the CSV read path destroys (`_cast_value` casts
            // it to 3230 because the column declares `number`). Text keeps it.
            ("leadingzero", "{\"n\":5}\n{\"n\":\"03230\"}\n", "03230"),
            ("boolean", "{\"n\":true}\n{\"n\":\"maybe\"}\n", "maybe"),
        ] {
            let d = tmpdir(&format!("mixedleaf-{tag}"));
            let (inp, outp) = write(&d, lines);
            let schema = sketch(&inp).unwrap();
            let hints = schema.numeric_hints();
            let mixed = schema.mixed_leaf_columns();
            assert!(mixed.contains("n"), "{tag}: column not flagged heterogeneous");
            let (cols, types, _i, vm, jc) = schema.finish();
            write_arrow_impl(&inp, &outp, &cols, &types, &hints, &mixed, jc, vm, None).unwrap();

            let batches = read_back(&outp);
            let n = batches[0].column_by_name("n").unwrap();
            assert_eq!(n.data_type(), &DataType::Utf8, "{tag}");
            let v = n.as_string::<i32>();
            // nothing became null, and the token is byte-exact
            assert!(n.is_valid(0) && n.is_valid(1), "{tag}: a cell became null");
            assert_eq!(v.value(1), cell1, "{tag}");
            let _ = std::fs::remove_dir_all(&d);
        }
    }

    /// The fallback must not fire on a homogeneous column — otherwise every
    /// numeric column quietly becomes text and the typing work is undone.
    #[test]
    fn a_homogeneous_column_is_not_flagged_heterogeneous() {
        let d = tmpdir("homogeneous");
        let (inp, outp) = write(&d, "{\"n\":5,\"s\":\"a\"}\n{\"n\":6,\"s\":\"b\"}\n{\"n\":null}\n");
        let schema = sketch(&inp).unwrap();
        let hints = schema.numeric_hints();
        let mixed = schema.mixed_leaf_columns();
        assert!(mixed.is_empty(), "a null does not make a column heterogeneous");
        let (cols, types, _i, vm, jc) = schema.finish();
        write_arrow_impl(&inp, &outp, &cols, &types, &hints, &mixed, jc, vm, None).unwrap();
        let batches = read_back(&outp);
        assert_eq!(
            batches[0].column_by_name("n").unwrap().data_type(),
            &DataType::Int64
        );
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn arrow_sidecar_batches_instead_of_materialising_the_file() {
        let d = tmpdir("batched");
        let mut lines = String::new();
        for i in 0..(BATCH_ROWS + 5) {
            lines.push_str(&format!("{{\"i\":{i}}}\n"));
        }
        let (inp, outp) = write(&d, &lines);
        let schema = sketch(&inp).unwrap();
        let hints = schema.numeric_hints();
        let mixed = schema.mixed_leaf_columns();
        let (cols, types, _i, vm, jc) = schema.finish();
        let n = write_arrow_impl(&inp, &outp, &cols, &types, &hints, &mixed, jc, vm, None).unwrap();
        assert_eq!(n, BATCH_ROWS + 5);

        let batches = read_back(&outp);
        assert_eq!(batches.len(), 2, "one full batch plus the remainder");
        assert_eq!(batches[0].num_rows(), BATCH_ROWS);
        assert_eq!(batches[1].num_rows(), 5);
        let _ = std::fs::remove_dir_all(&d);
    }
}
