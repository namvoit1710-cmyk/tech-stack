//! Streaming JSON(L) -> CSV for the SMDG-AI claim-check large-data path (SA-1905).
//!
//! The control plane's `dict_to_csv` builds the whole column union and every
//! row in RAM (measured 5.5-9.3x blow-up + a 128 MB/cell ceiling), so a 500 MB
//! worker output OOMs a 128 MB worker. This crate does the same flatten, but
//! **streaming**: the worker writes its output as JSON Lines (one JSON object
//! per line) to a temp file, then this crate makes two bounded passes over that
//! file — PASS 1 sketches the schema (A1), PASS 2 writes the CSV rows (B1) —
//! holding only one row plus the schema in memory at a time.
//!
//! Byte-for-byte identity with `CsvConverter` is **not** required, only
//! round-trip compatibility: the cells are read back by `csv_to_dict` ->
//! `_unflatten` -> `_cast_value`, which `json.loads`/casts each cell, so
//! whitespace/`true` vs `True` differences wash out. The flatten rules match
//! `_flatten`: nested object -> dotted columns; array -> one JSON cell; null ->
//! empty; scalars -> their string form. A synthetic integer `__row_id` column
//! is written first so the output is upload-ready (the file service requires
//! it) without a second full-RAM injection pass.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde_json::{Map, Value};
use std::collections::{HashMap, HashSet};
use std::fs::File;
use std::io::{BufRead, BufReader, BufWriter};

const ROW_ID: &str = "__row_id";
/// Same constant, visible to sibling modules.
pub(crate) const ROW_ID_PUB: &str = ROW_ID;

/// SA-1977 A4 — the reserved column holding a scalar / list-of-scalar root.
/// MUST match `value_kind.VALUE_COLUMN` on the control plane: this crate and
/// the Python `CsvConverter` are two writers of one format, and a corpus that
/// only ran against one of them would not notice them drifting apart.
const VALUE_COLUMN: &str = "__value";

const K_ARRAY: &str = "array";
const K_OBJECT: &str = "object";
const K_STRING: &str = "string";
const K_NUMBER: &str = "number";
const K_BOOLEAN: &str = "boolean";
const K_NULL: &str = "null";
const K_UNKNOWN: &str = "unknown";

/// The root kind of one JSON value — the Rust twin of `value_kind.kind_of_inline`.
fn root_kind(v: &Value) -> &'static str {
    match v {
        Value::Object(_) => K_OBJECT,
        Value::Array(_) => K_ARRAY,
        Value::String(_) => K_STRING,
        Value::Number(_) => K_NUMBER,
        Value::Bool(_) => K_BOOLEAN,
        Value::Null => K_NULL,
    }
}

/// The physical types a numeric column can take WITHOUT losing a digit.
const NUM_INT: &str = "int";
const NUM_FLOAT: &str = "float";
const NUM_TEXT: &str = "text";

/// SA-1905 — how a numeric column can be stored losslessly.
///
/// The declared kind `number` is all the CSV needs, because CSV writes the token
/// verbatim and cannot lose a digit. A *typed* consumer needs more: `Float64`
/// silently collapses 2^53+1 onto 2^53, and an id is exactly the kind of value
/// that lives above that line — a Snowflake id is 19 digits. So PASS 1 also
/// tracks, per numeric column, the three facts that decide a safe physical type.
///
/// This costs one small integer per numeric column and nothing per row; the CSV
/// path never reads it.
#[derive(Default)]
struct NumInfo {
    /// An integer literal that does not fit i64 — no fixed-width type is safe.
    saw_big: bool,
    /// A token carrying a fraction or an exponent.
    saw_float: bool,
    /// An integer literal was seen. Distinct from `max_abs_int > 0` because `0`
    /// is an integer. Only the nested writer reads it — see
    /// `nested::ObservedNode::resolve`, where a column holding BOTH integer and
    /// fractional literals must keep the literals rather than become `Float64`:
    /// `_cast_value` tries `int()` first, so CSV reads `1` back as `int` and
    /// `1.0` as `float`, and `Float64` cannot tell those two apart.
    saw_int: bool,
    /// Largest |integer| seen, which decides whether mixing with floats is safe.
    max_abs_int: u64,
}

impl NumInfo {
    /// Fold one numeric token in. Takes `&str` and allocates nothing — this runs
    /// once per numeric CELL, i.e. millions of times on a real payload.
    fn observe(&mut self, token: &str) {
        if let Ok(i) = token.parse::<i64>() {
            self.saw_int = true;
            self.max_abs_int = self.max_abs_int.max(i.unsigned_abs());
        } else if token.contains('.') || token.contains('e') || token.contains('E') {
            self.saw_float = true;
        } else {
            // An integer literal i64 cannot hold. Only `arbitrary_precision`
            // makes this reachable — without it serde would already have turned
            // it into an f64 and the digits would be gone before we saw them.
            self.saw_big = true;
        }
    }

    /// The widest lossless choice, and never wider than lossless.
    fn hint(&self) -> &'static str {
        if self.saw_big {
            return NUM_TEXT;
        }
        if !self.saw_float {
            return NUM_INT;
        }
        // Mixed integers and floats: one column, one type, so it has to be f64 —
        // which is exact for every integer up to 2^53. Above that the only
        // lossless option is the token itself, i.e. the choice CSV already makes.
        if self.max_abs_int <= (1u64 << 53) {
            NUM_FLOAT
        } else {
            NUM_TEXT
        }
    }
}

/// Ordered column union + first-seen leaf types, accumulated across all rows.
struct Schema {
    columns: Vec<String>,
    seen: HashSet<String>,
    types: HashMap<String, String>,
    /// Per numeric column, what it can be stored as. Empty for a file with no
    /// numbers; consulted only by a typed writer.
    num_info: HashMap<String, NumInfo>,
    /// Columns that held more than one non-null leaf type across the file. The
    /// CSV does not care — it stores text either way — but a typed sidecar must
    /// fall back to text for these or it silently nulls whatever does not parse.
    mixed_leaf: HashSet<String>,
    row_count: usize,
    /// True once any line is NOT a JSON object: the whole file then stores its
    /// values under `__value` (mirrors `DataStoreService._is_value_root` —
    /// "a list with ANY non-dict element"). Deciding this per line instead of
    /// per file is what would emit a leaf column and a branch column at the
    /// same path.
    value_mode: bool,
    /// The union of the per-line root kinds (nulls ignored) — `item_kind`.
    item_kinds: HashSet<&'static str>,
}

impl Schema {
    fn new() -> Self {
        Schema {
            columns: Vec::new(),
            seen: HashSet::new(),
            types: HashMap::new(),
            num_info: HashMap::new(),
            mixed_leaf: HashSet::new(),
            row_count: 0,
            value_mode: false,
            item_kinds: HashSet::new(),
        }
    }

    fn observe(&mut self, key: &str, ty: Option<&str>) {
        if !self.seen.contains(key) {
            self.seen.insert(key.to_string());
            self.columns.push(key.to_string());
        }
        let Some(t) = ty else { return };
        // First non-null observation per column wins — unchanged, and it has to
        // stay that way because `_infer_into` on the control plane does the same.
        //
        // But a declared type is only the FIRST row's leaf type, not a promise
        // about the rest, and `[{"n":5},{"n":"N/A"}]` is ordinary API data. CSV
        // absorbs that (it stores text and `_cast_value` falls back to the raw
        // value when the cast fails); a typed column cannot. So remember when a
        // column was not homogeneous — see `mixed_leaf_columns`.
        //
        // `get` before `insert` rather than `entry(key.to_string())`: `entry`
        // needs an owned key even on a hit, i.e. a String allocation per cell
        // per row, on the hottest loop in the crate.
        match self.types.get(key) {
            None => {
                self.types.insert(key.to_string(), t.to_string());
            }
            Some(existing) => {
                if existing != t && !self.mixed_leaf.contains(key) {
                    self.mixed_leaf.insert(key.to_string());
                }
            }
        }
    }

    /// Classify one numeric token for its column. The token is the same string
    /// the CSV cell gets (`serde_json::Number::to_string`), so this reads the
    /// value the writer actually emits rather than a re-parse of it.
    ///
    /// The lookup is deliberately NOT `entry(key.to_string())`: `entry` needs an
    /// owned key even on a hit, so that shape allocates a `String` per numeric
    /// cell per row. Measured on 200k rows x 4 numeric columns it cost **34% of
    /// the whole PASS-1 scan**, and it charged that to the CSV path — which does
    /// not use any of this. Allocate once per NEW column instead.
    fn observe_number(&mut self, key: &str, token: &str) {
        if let Some(e) = self.num_info.get_mut(key) {
            e.observe(token);
            return;
        }
        let mut e = NumInfo::default();
        e.observe(token);
        self.num_info.insert(key.to_string(), e);
    }

    /// `{column: int|float|text}` for every numeric column.
    ///
    /// Read BEFORE [`Schema::finish`], which consumes the schema. Deliberately
    /// not part of `finish`'s tuple: the CSV writer has no use for it, and the
    /// two producers must not start disagreeing about what `finish` returns.
    /// Columns whose leaf type was not the same on every non-null row.
    ///
    /// Read BEFORE [`Schema::finish`]. A typed writer must store these as text:
    /// `[{"n":5},{"n":"N/A"}]` declares `number`, and an Int64 column can hold
    /// `5` but not `N/A` — which becomes a silent null, i.e. total loss of a
    /// value the CSV twin keeps. Text is lossless and is what the CSV stores at
    /// rest anyway.
    fn mixed_leaf_columns(&self) -> HashSet<String> {
        self.mixed_leaf.clone()
    }

    fn numeric_hints(&self) -> HashMap<String, &'static str> {
        self.num_info
            .iter()
            .map(|(k, v)| (k.clone(), v.hint()))
            .collect()
    }

    /// The element kind of the array this JSONL file represents. `unknown` when
    /// the lines are of mixed kinds; `null` when every line was null.
    ///
    /// `object` ONLY when every line is an object — the twin of the same
    /// asymmetry in `value_kind.item_kind_of_inline`, and load-bearing for the
    /// same reason: `object` is exactly "the rows ARE the columns", i.e. NOT the
    /// `__value` layout. Letting nulls abstain here would declare a sparse
    /// `[{...}, null, {...}]` as `array<object>` while `value_mode` stored it in
    /// `__value`, and the reader would then refuse to unwrap what we wrapped.
    fn item_kind(&self) -> &'static str {
        if self.item_kinds.is_empty() {
            return K_UNKNOWN; // no lines at all
        }
        if self.item_kinds.len() == 1 && self.item_kinds.contains(K_OBJECT) {
            return K_OBJECT;
        }
        let non_null: Vec<&&str> = self.item_kinds.iter().filter(|k| **k != K_NULL).collect();
        if non_null.is_empty() {
            return K_NULL;
        }
        if non_null.len() == 1 && *non_null[0] != K_OBJECT {
            return non_null[0];
        }
        K_UNKNOWN
    }

    /// True when every `__value` cell must be JSON-encoded — the twin of
    /// `CsvConverter.value_cells_are_json`. A mixed list needs one column type to
    /// cover every element; a STRING list containing a null needs `null` and `""`
    /// to be written distinctly, or `["a", null, "b"]` reads back as
    /// `["a", "", "b"]`.
    fn json_cells(&self) -> bool {
        let item = self.item_kind();
        item == K_UNKNOWN || (item == K_STRING && self.item_kinds.contains(K_NULL))
    }

    /// Finalise into `(columns, types, item_kind, value_mode, json_cells)`. In
    /// value mode the object columns accumulated so far are discarded in favour of
    /// the single reserved column, whose declared type mirrors
    /// `CsvConverter.infer_value_column_types`.
    pub(crate) fn finish(self) -> (Vec<String>, HashMap<String, String>, &'static str, bool, bool) {
        let item = self.item_kind();
        let json_cells = self.json_cells();
        if !self.value_mode {
            return (self.columns, self.types, item, false, false);
        }
        let mut types: HashMap<String, String> = HashMap::new();
        if json_cells {
            types.insert(VALUE_COLUMN.to_string(), "json".to_string());
        } else {
            match item {
                // All-null: leave the column type unlocked so it reads back as None.
                K_NULL => {}
                // A homogeneous list of structures: one `json` type covers them.
                K_ARRAY | K_OBJECT => {
                    types.insert(VALUE_COLUMN.to_string(), "json".to_string());
                }
                other => {
                    types.insert(VALUE_COLUMN.to_string(), other.to_string());
                }
            }
        }
        (vec![VALUE_COLUMN.to_string()], types, item, true, json_cells)
    }
}

/// Flatten one JSON object into `(dotted_key, cell, leaf_type)` triples,
/// matching `CsvConverter._flatten`. `leaf_type` is `None` for null (leave the
/// column type unlocked so a later non-null row can decide it).
fn flatten_object(prefix: &str, obj: &Map<String, Value>, out: &mut Vec<(String, String, Option<&'static str>)>) {
    for (k, v) in obj {
        let key = if prefix.is_empty() {
            k.clone()
        } else {
            format!("{prefix}.{k}")
        };
        match v {
            Value::Object(m) => flatten_object(&key, m, out),
            Value::Array(_) => out.push((key, v.to_string(), Some("json"))),
            Value::Null => out.push((key, String::new(), None)),
            Value::Bool(b) => out.push((key, b.to_string(), Some("boolean"))),
            Value::Number(n) => out.push((key, n.to_string(), Some("number"))),
            Value::String(s) => out.push((key, s.clone(), Some("string"))),
        }
    }
}

/// The `__value` cell for a non-object line — the Rust twin of
/// `CsvConverter._value_cell`. In `mixed` mode EVERY element is JSON-encoded so
/// the column has one declared type and still round-trips each element exactly;
/// otherwise scalars are written in their plain form (so the steward sees `1`,
/// not `1` quoted) and structures are JSON-encoded.
fn value_cell(v: &Value, mixed: bool) -> String {
    if mixed {
        return v.to_string();
    }
    match v {
        Value::Null => String::new(),
        Value::Bool(b) => b.to_string(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => s.clone(),
        _ => v.to_string(),
    }
}

/// Parse one JSONL line into a flat cell list, for the PASS-2 object path.
fn flatten_line(line: &str) -> Result<Vec<(String, String, Option<&'static str>)>, String> {
    let v: Value = parse_line(line)?;
    let mut out = Vec::new();
    match v {
        Value::Object(m) => flatten_object("", &m, &mut out),
        // Not reachable in object mode (PASS 1 would have set value_mode), but
        // never panic on an odd input: fall back to the reserved column.
        other => out.push((VALUE_COLUMN.to_string(), value_cell(&other, false), None)),
    }
    Ok(out)
}

fn parse_line(line: &str) -> Result<Value, String> {
    serde_json::from_str(line).map_err(|e| format!("invalid JSON line: {e}"))
}

pub(crate) fn open_lines(path: &str) -> PyResult<impl Iterator<Item = std::io::Result<String>>> {
    let file = File::open(path).map_err(|e| PyValueError::new_err(format!("cannot open {path}: {e}")))?;
    Ok(BufReader::new(file).lines())
}

/// PASS 1 (A1) — stream the JSONL file and return the ordered column union, the
/// `{column: type}` schema, the row count, and (SA-1977) the `item_kind`: the
/// element kind of the array this file represents. Bounded RAM: one line + the
/// schema at a time. Sparse fields first seen at row N still make the union.
///
/// The `item_kind` is the fact the control plane records on the file_ref as
/// `item_kind` (its `value_kind` is `array` — a JSONL file *is* a list). Before
/// SA-1977 this pass computed the shape of every line and then threw the root
/// type away, exactly like `dict_to_csv` did on the Python side.
pub(crate) fn sketch(path: &str) -> PyResult<Schema> {
    sketch_into(path, None)
}

/// PASS 1, optionally feeding a SECOND observer from the same parse.
///
/// The tree sketch ([`nested::ObservedNode`]) answers a different question than
/// the flat one and cannot be derived from it — `flatten_object` has already
/// destroyed the nesting by the time `Schema` sees a cell. But both questions
/// are answered from the same `Value`, and parsing is the dominant cost (the
/// merged CSV+Arrow writer measured +48% when it re-read the file instead of
/// re-using the parse). So: one read, one parse, two sketches.
///
/// `tree` is `None` on the CSV-only path, which therefore pays nothing.
fn sketch_into(path: &str, mut tree: Option<&mut nested::ObservedNode>) -> PyResult<Schema> {
    let mut schema = Schema::new();
    for line in open_lines(path)? {
        let line = line.map_err(|e| PyValueError::new_err(format!("read error: {e}")))?;
        if line.trim().is_empty() {
            continue;
        }
        let v = parse_line(&line).map_err(PyValueError::new_err)?;
        if let Some(t) = tree.as_deref_mut() {
            t.observe(&v, 0);
        }
        schema.item_kinds.insert(root_kind(&v));
        match &v {
            Value::Object(m) => {
                let mut cells = Vec::new();
                flatten_object("", m, &mut cells);
                for (k, cell, ty) in &cells {
                    schema.observe(k, *ty);
                    if *ty == Some(K_NUMBER) {
                        schema.observe_number(k, cell);
                    }
                }
            }
            other => {
                schema.value_mode = true;
                // A `list[number]` is stored in the reserved column, so that
                // column needs the same classification an object column gets.
                if let Value::Number(n) = other {
                    schema.observe_number(VALUE_COLUMN, &n.to_string());
                }
            }
        }
        schema.row_count += 1;
    }
    Ok(schema)
}

/// Every exported function releases the GIL for the whole conversion.
///
/// Measured before this existed: converting a 561 MB payload froze the entire
/// Python interpreter for **39.7 s** — a ticker thread that should have fired
/// ~396 times at 100 ms fired twice. Nothing else in the process ran: not the
/// worker heartbeat (`HEARTBEAT_INTERVAL_SECONDS` is 30, so at least one beat is
/// missed), not lease renewal — which is an asyncio task and so cannot fire no
/// matter what `lease_ttl_seconds` the executor set — and not any other task the
/// same worker had in flight.
///
/// A lease that lapses is not a slow task, it is a task the executor hands to
/// somebody else while this worker is still running it.
///
/// This costs nothing and buys nothing in throughput; the conversion takes
/// exactly as long. It only stops that time from being taken from everyone else.
/// Paths are copied to owned `String`s first so no Python-derived borrow crosses
/// the release, which keeps the safety argument local instead of resting on what
/// `Ungil` happens to permit.
#[pyfunction]
fn schema_sketch(
    py: Python<'_>,
    path: &str,
) -> PyResult<(Vec<String>, HashMap<String, String>, usize, String)> {
    let path = path.to_owned();
    py.allow_threads(move || {
        let schema = sketch(&path)?;
        let row_count = schema.row_count;
        let (columns, types, item_kind, _value_mode, _json_cells) = schema.finish();
        Ok((columns, types, row_count, item_kind.to_string()))
    })
}

/// PASS 2 (B1) — stream the JSONL file again and write a CSV with header
/// `[__row_id, <columns>]`, one record per line, missing cells emitted empty.
/// Returns the row count written. Bounded RAM: one row at a time.
///
/// `value_mode` (SA-1977 A4): write each line WHOLE into the single reserved
/// `__value` column instead of flattening it — that is how a `list[scalar]` stays
/// storable as CSV. `json_cells` mirrors `CsvConverter.value_cells_are_json`.
///
/// Both are PASSED IN, never re-derived. PASS 1 already knows them. Inferring
/// `value_mode` back from the column names here (`columns == ["__value"]`) looked
/// equivalent and was not: a list of one-key `{"__value": N}` dicts legitimately
/// produces the column `__value` in OBJECT mode, and the sniff then wrote the
/// whole JSON line into the cell — bytes at rest that contradict their own
/// declared column type. Re-deriving a fact the producer already computed is the
/// bug this crate's caller exists to remove; it is no better in Rust.
/// The GIL-free body. Kept separate from the `#[pyfunction]` so `jsonl_to_csv`
/// can call it from INSIDE its own `allow_threads` — a nested release is not
/// possible (there is no `Python` token to release with once the GIL is gone),
/// and re-acquiring it between the two passes would reintroduce exactly the
/// stall this split removes.
pub(crate) fn write_csv_impl(
    in_path: &str,
    out_path: &str,
    columns: &[String],
    json_cells: bool,
    value_mode: bool,
) -> PyResult<usize> {
    let out = File::create(out_path)
        .map_err(|e| PyValueError::new_err(format!("cannot create {out_path}: {e}")))?;
    let mut writer = csv::Writer::from_writer(BufWriter::new(out));

    let mut header: Vec<&str> = Vec::with_capacity(columns.len() + 1);
    header.push(ROW_ID);
    header.extend(columns.iter().map(|c| c.as_str()));
    writer
        .write_record(&header)
        .map_err(|e| PyValueError::new_err(format!("csv header write failed: {e}")))?;

    let mut row_index: usize = 0;
    for line in open_lines(in_path)? {
        let line = line.map_err(|e| PyValueError::new_err(format!("read error: {e}")))?;
        if line.trim().is_empty() {
            continue;
        }
        let mut record: Vec<String> = Vec::with_capacity(columns.len() + 1);
        record.push(row_index.to_string());

        if value_mode {
            let v = parse_line(&line).map_err(PyValueError::new_err)?;
            record.push(value_cell(&v, json_cells));
        } else {
            let cells = flatten_line(&line).map_err(PyValueError::new_err)?;
            let by_key: HashMap<&str, &str> =
                cells.iter().map(|(k, cell, _)| (k.as_str(), cell.as_str())).collect();
            for col in columns {
                record.push(by_key.get(col.as_str()).copied().unwrap_or("").to_string());
            }
        }
        writer
            .write_record(&record)
            .map_err(|e| PyValueError::new_err(format!("csv row write failed: {e}")))?;
        row_index += 1;
    }
    writer
        .flush()
        .map_err(|e| PyValueError::new_err(format!("csv flush failed: {e}")))?;
    Ok(row_index)
}

#[pyfunction]
#[pyo3(signature = (in_path, out_path, columns, json_cells = false, value_mode = false))]
fn write_csv(
    py: Python<'_>,
    in_path: &str,
    out_path: &str,
    columns: Vec<String>,
    json_cells: bool,
    value_mode: bool,
) -> PyResult<usize> {
    let (in_path, out_path) = (in_path.to_owned(), out_path.to_owned());
    py.allow_threads(move || write_csv_impl(&in_path, &out_path, &columns, json_cells, value_mode))
}

/// Convenience: PASS 1 + PASS 2 in one call. Returns `(columns, column_types,
/// row_count, item_kind)` — the metadata the control plane records on the
/// file_ref. The `value_kind` is not returned because it is not in question: a
/// JSONL file is a list of its lines, so the root is always `array`.
#[pyfunction]
fn jsonl_to_csv(
    py: Python<'_>,
    in_path: &str,
    out_path: &str,
) -> PyResult<(Vec<String>, HashMap<String, String>, usize, String)> {
    let (in_path, out_path) = (in_path.to_owned(), out_path.to_owned());
    // ONE release spanning both passes — see `schema_sketch`. This is the call
    // the worker actually makes, and the one measured at 39.7 s of frozen
    // interpreter before the release existed.
    py.allow_threads(move || {
        let schema = sketch(&in_path)?;
        let (columns, types, item_kind, value_mode, json_cells) = schema.finish();
        let row_count = write_csv_impl(&in_path, &out_path, &columns, json_cells, value_mode)?;
        Ok((columns, types, row_count, item_kind.to_string()))
    })
}

/// The TREE sketch — what the flat `Schema` throws away. See the module docs.
/// One place that maps a codec NAME to `IpcWriteOptions`, so the four Arrow
/// writers in this crate cannot disagree about what "zstd" means.
mod codec;

mod nested;

/// SA-1910 — the recursive nested-array CSV-tree writer, shared by the control
/// plane and the worker SDK behind a manifest. See `split.rs`.
mod split;

/// Writing a [`nested::Shape`] tree as nested Arrow. See the module docs.
mod nested_arrow;

/// The nested Arrow file: write it, read it back to Python. See the module docs.
mod nested_io;

/// The typed Arrow read path alongside the CSV. See the module docs.
pub mod arrow_sidecar;

#[pymodule]
fn json_csv_streamer(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(schema_sketch, m)?)?;
    m.add_function(wrap_pyfunction!(write_csv, m)?)?;
    m.add_function(wrap_pyfunction!(jsonl_to_csv, m)?)?;

    // Arrow sidecar (prototype): same scan, a second typed artifact, read back
    // batch-at-a-time over the Arrow C Data Interface instead of re-parsed.
    m.add_function(wrap_pyfunction!(arrow_sidecar::jsonl_to_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(arrow_sidecar::jsonl_to_csv_and_arrow, m)?)?;
    m.add_class::<arrow_sidecar::ArrowFileStream>()?;

    // The NESTED path: one file holds the whole recursive value, so the read
    // side needs no split, no __parent_row_id join and no re-parse.
    m.add_function(wrap_pyfunction!(nested_io::jsonl_to_nested_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(nested_io::jsonl_to_csv_and_nested_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(nested_io::arrow_bytes_to_rows, m)?)?;
    m.add_function(wrap_pyfunction!(split::jsonl_split_recursive, m)?)?;

    // MUST equal `data_reference.FILE_REF_MARKER`. A nested file_ref is a
    // POINTER; the tree sketch refuses to recurse into one, and the control
    // plane asserts the two spellings agree.
    m.add("FILE_REF_MARKER", nested::FILE_REF_MARKER)?;

    // SA-1905 F3 — the reserved column names this crate writes, exported so the
    // control plane can ASSERT they match `value_kind.VALUE_COLUMN` and
    // `csv_converter.ROW_ID_COLUMN` instead of trusting the comment above each
    // constant. Two writers of one format cannot be kept in step by a docstring;
    // a renamed column on either side is a corpus of unreadable files, and the
    // only cheap moment to catch it is a test that can see both values at once.
    m.add("VALUE_COLUMN", VALUE_COLUMN)?;
    m.add("ROW_ID_COLUMN", ROW_ID)?;

    // The kind vocabulary `item_kind` is drawn from, for the same reason.
    m.add(
        "ITEM_KINDS",
        vec![
            K_ARRAY, K_OBJECT, K_STRING, K_NUMBER, K_BOOLEAN, K_NULL, K_UNKNOWN,
        ],
    )?;
    Ok(())
}

#[cfg(test)]
mod tests_parity;

#[cfg(test)]
mod tests_codec;
