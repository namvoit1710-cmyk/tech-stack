//! SA-1910 — the recursive nested-array split, in Rust, behind a manifest.
//!
//! # Why this exists
//!
//! Nested JSON is stored as a TREE of CSVs, because CSV has no nested types:
//! `{"orders":[{"id":1,"items":[…]}]}` becomes three files joined on
//! `__row_id`/`__parent_row_id`. Until now that writer lived only in the
//! control plane (`_store_rows_recursive` + `prune_arrays_of_dicts`), while the
//! matching READER had been written twice in Python — once in the control plane
//! (`_reconstruct_recursive`) and once in the worker SDK
//! (`http_file_ref_resolver`). One writer, two readers, three chances to drift.
//!
//! Moving the writer here lets the control plane and the worker SDK share it,
//! which turns "the two sides agree because a test says so" into "the two sides
//! agree because there is one of them". It also removes the reason a large node
//! output has to ride an Event Mesh message at all: a worker that can build the
//! tree itself publishes a pointer, not 2.3 MB of rows.
//!
//! # The seam
//!
//! Rust does the part that is pure CPU over bytes — read JSONL, prune, stamp,
//! write N CSVs — and returns a MANIFEST. Python keeps the part that needs
//! credentials and async IO: uploading each CSV and assembling the `file_ref`
//! (`build_file_ref` / `add_artifact`). The manifest mirrors the artifact tree
//! one-for-one, so the Python side is a walk, not a re-derivation.
//!
//! # Three contracts this MUST reproduce exactly
//!
//! 1. **`__row_id` is 0-based and is not written here.** `write_csv_impl`
//!    already emits it as the first column, counting from 0 — the same scheme
//!    `_store_rows_recursive` stamps by hand (`pruned["__row_id"] = idx`) so it
//!    matches the children's `__parent_row_id`. Stamping it again would produce
//!    the column twice. `_upload_csv`'s `inject_row_ids` skips rows that already
//!    carry one, so its 1-based scheme still does not interfere.
//!
//! 2. **`__parent_row_id` is stamped LAST on each child row**, exactly as
//!    `c = dict(child); c["__parent_row_id"] = idx` does — insertion order is
//!    column order, and the CP's re-join reads it by name but the FE reads the
//!    declared `columns` list positionally in places.
//!
//! 3. **Only arrays whose elements are ALL objects are extracted.** Empty
//!    arrays, scalar arrays and mixed arrays stay inline as a JSON cell, and a
//!    nested `__file_ref` object is left whole (F1). That is what makes the
//!    split lossless, and it is also the escape hatch used when a guard below
//!    declines to extract.
//!
//! # What is deliberately NOT derived from the data
//!
//! Local file names. A field key is attacker-controlled in the sense that it
//! comes from whatever JSON a node produced, so `"../../etc/passwd"` or a
//! 4 KB key must never reach the filesystem. Every file written here is
//! `n<seq>.jsonl` / `n<seq>.csv`; the logical dotted path travels in the
//! manifest's `name`, where Python turns it into the file-service filename the
//! same way it always has.

use std::collections::HashMap;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::{Path, PathBuf};

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

use crate::nested::FILE_REF_MARKER;
use crate::{open_lines, sketch, write_csv_impl};

/// Column the child rows carry back to their parent's `__row_id`.
pub(crate) const PARENT_ROW_ID: &str = "__parent_row_id";

/// How many levels of array nesting are split into their own tables.
///
/// The Python original has no cap because Python's recursion is heap-allocated
/// and it was only ever fed node output. Rust's is not, and this function is
/// reachable from a worker. Past the cap the array simply stays inline — the
/// same lossless outcome a scalar array already gets — so exceeding it costs
/// query-ability, never data.
pub(crate) const MAX_SPLIT_DEPTH: usize = 8;

/// Distinct child tables per level. A JSON object used as a MAP (one key per
/// customer id, each holding a list) would otherwise open one file per key.
pub(crate) const MAX_CHILD_TABLES: usize = 64;

/// Object nesting `prune` will walk before treating the object as a leaf.
pub(crate) const MAX_OBJECT_DEPTH: usize = 32;

/// One node of the artifact tree: a CSV on disk plus everything the control
/// plane records about it on the `file_ref`.
#[derive(Debug)]
pub(crate) struct Node {
    /// Dotted path RELATIVE TO THE PARENT (`""` for the root), because that is
    /// what `add_artifact(name=path)` stores and what the re-join looks up.
    pub name: String,
    pub csv_path: PathBuf,
    /// Exactly the CSV's header, `__row_id` included.
    ///
    /// It has to be: `_extract_columns` flattens a row that has already been
    /// stamped, so the control plane's `columns` carries `__row_id` today
    /// (measured: `['a','b','__parent_row_id','__row_id']`) — the comment at
    /// `data_store_service.py:174` claiming it is omitted is stale. The data
    /// factory identifies rows BY `__row_id`, so dropping it from the declared
    /// columns would take the identity away from the editor.
    ///
    /// Position differs from the Python writer by construction and that is
    /// fine: each `columns` describes ITS OWN file, and `write_csv_impl` puts
    /// `__row_id` first while `dict_to_csv` puts it last.
    pub columns: Vec<String>,
    pub column_types: HashMap<String, String>,
    pub row_count: usize,
    pub item_kind: String,
    /// Size of `csv_path`, so `build_file_ref(size_bytes=...)` and the OOM
    /// guard's declared-size branch need no stat and no read.
    pub size_bytes: u64,
    /// `sha256:<hex>` over the CSV bytes — the value both callers already use
    /// as `X-Idempotency-Key`. Carried so the upload can stream from the file
    /// instead of materialising it to compute its own key.
    pub content_key: String,
    pub children: Vec<Node>,
}

impl Node {
    fn to_py<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        d.set_item("name", &self.name)?;
        d.set_item("csv_path", self.csv_path.to_string_lossy().to_string())?;
        d.set_item("columns", self.columns.clone())?;
        d.set_item("column_types", self.column_types.clone())?;
        d.set_item("row_count", self.row_count)?;
        d.set_item("item_kind", &self.item_kind)?;
        d.set_item("size_bytes", self.size_bytes)?;
        d.set_item("content_key", &self.content_key)?;
        let kids = PyList::empty(py);
        for c in &self.children {
            kids.append(c.to_py(py)?)?;
        }
        d.set_item("children", kids)?;
        Ok(d)
    }
}

/// `is_file_ref` — deliberately `== Value::Bool(true)`, not "the key exists".
/// `data_reference.is_file_ref` is `value.get(FILE_REF_MARKER) is True`, and a
/// row holding `{"__file_ref": "no"}` is ordinary data that must still split.
fn is_file_ref(m: &Map<String, Value>) -> bool {
    matches!(m.get(FILE_REF_MARKER), Some(Value::Bool(true)))
}

fn all_objects(items: &[Value]) -> bool {
    !items.is_empty() && items.iter().all(|v| v.is_object())
}

/// The Rust twin of `CsvConverter.prune_arrays_of_dicts`.
///
/// Splits one row into (row without its arrays-of-objects, {dotted path: array}).
/// Recurses into plain objects so `{"order": {"lines": [...]}}` yields the array
/// under `"order.lines"` and keeps `order`'s scalars in the pruned row.
fn prune_into(
    src: &Map<String, Value>,
    dst: &mut Map<String, Value>,
    arrays: &mut Map<String, Value>,
    prefix: &str,
    obj_depth: usize,
    may_extract: bool,
) {
    for (k, v) in src {
        let path = if prefix.is_empty() {
            k.clone()
        } else {
            format!("{prefix}.{k}")
        };
        match v {
            Value::Array(items) if may_extract && all_objects(items) => {
                // Extracted: it does NOT go into the pruned row. Insertion into
                // an ordered map, so a duplicate path keeps its first position
                // and takes the last value — Python dict assignment exactly.
                arrays.insert(path, v.clone());
            }
            Value::Object(m) if !is_file_ref(m) && obj_depth < MAX_OBJECT_DEPTH => {
                let mut sub = Map::new();
                prune_into(m, &mut sub, arrays, &path, obj_depth + 1, may_extract);
                // `dst[k] = sub` happens even when sub is empty, matching the
                // original: an object whose every field was extracted still
                // leaves an empty object behind rather than vanishing.
                dst.insert(k.clone(), Value::Object(sub));
            }
            _ => {
                dst.insert(k.clone(), v.clone());
            }
        }
    }
}

fn io_err<E: std::fmt::Display>(what: &str, e: E) -> String {
    format!("{what}: {e}")
}

/// `(size, "sha256:<hex>")` over a file, without holding it in memory.
fn digest_file(path: &Path) -> Result<(u64, String), String> {
    use std::io::Read;
    let mut f = File::open(path).map_err(|e| io_err("cannot reopen csv", e))?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 64 * 1024];
    let mut total: u64 = 0;
    loop {
        let n = f.read(&mut buf).map_err(|e| io_err("csv read failed", e))?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
        total += n as u64;
    }
    Ok((total, format!("sha256:{:x}", h.finalize())))
}

/// An open child table: its logical name, its JSONL spool, and its writer.
struct Child {
    name: String,
    jsonl: PathBuf,
    writer: BufWriter<File>,
}

fn next_path(dir: &Path, ctr: &mut usize, ext: &str) -> PathBuf {
    let p = dir.join(format!("n{ctr}.{ext}"));
    *ctr += 1;
    p
}

/// Split one level: rewrite `in_jsonl` without its arrays-of-objects, spool each
/// extracted array to its own JSONL, then write this level's CSV and recurse.
///
/// Streaming on purpose. The Python original materialises `pruned_rows` and
/// `per_path` for the WHOLE level before writing anything, so peak RAM is the
/// level's full contents; here nothing outlives one row except the open child
/// writers.
fn split_level(
    in_jsonl: &Path,
    dir: &Path,
    name: String,
    ctr: &mut usize,
    depth: usize,
) -> Result<Node, String> {
    let pruned_path = next_path(dir, ctr, "jsonl");
    let mut pruned_w = BufWriter::new(
        File::create(&pruned_path).map_err(|e| io_err("cannot create spool", e))?,
    );
    let mut kids: Vec<Child> = Vec::new();
    let may_extract = depth < MAX_SPLIT_DEPTH;

    let in_str = in_jsonl.to_string_lossy().to_string();
    let mut idx: usize = 0;
    for line in open_lines(&in_str).map_err(|e| format!("{e}"))? {
        let line = line.map_err(|e| io_err("read error", e))?;
        if line.trim().is_empty() {
            continue;
        }
        let v: Value = serde_json::from_str(&line).map_err(|e| io_err("bad JSONL line", e))?;
        let obj = match v {
            Value::Object(m) => m,
            // The caller only reaches the recursive split when every row is an
            // object (`all(isinstance(x, dict) for x in rows)`), and a child
            // table is all-objects by construction. Anything else is a caller
            // bug, and guessing would write a tree that cannot be re-joined.
            other => {
                return Err(format!(
                    "recursive split needs objects; row {idx} of {name:?} is {}",
                    match other {
                        Value::Null => "null",
                        Value::Bool(_) => "a boolean",
                        Value::Number(_) => "a number",
                        Value::String(_) => "a string",
                        Value::Array(_) => "an array",
                        Value::Object(_) => unreachable!(),
                    }
                ))
            }
        };

        let mut pruned = Map::new();
        let mut arrays = Map::new();
        prune_into(&obj, &mut pruned, &mut arrays, "", 0, may_extract);

        serde_json::to_writer(&mut pruned_w, &Value::Object(pruned))
            .map_err(|e| io_err("spool write failed", e))?;
        pruned_w
            .write_all(b"\n")
            .map_err(|e| io_err("spool write failed", e))?;

        for (path, arr) in arrays {
            let pos = match kids.iter().position(|c| c.name == path) {
                Some(p) => p,
                None => {
                    if kids.len() >= MAX_CHILD_TABLES {
                        // Refused, not degraded. By the time the budget is seen
                        // this row has already been pruned and written, so the
                        // array cannot be put back inline — the only honest
                        // choices left are "error" and "silently drop a table".
                        // Both callers still have the Python implementation to
                        // fall back to, and it has no such cap.
                        return Err(format!(
                            "level {name:?} wants more than {MAX_CHILD_TABLES} child tables"
                        ));
                    }
                    let jsonl = next_path(dir, ctr, "jsonl");
                    let w = BufWriter::new(
                        File::create(&jsonl).map_err(|e| io_err("cannot create child spool", e))?,
                    );
                    kids.push(Child { name: path.clone(), jsonl, writer: w });
                    kids.len() - 1
                }
            };
            let items = match arr {
                Value::Array(items) => items,
                _ => unreachable!("prune_into only inserts arrays"),
            };
            for child in items {
                let mut c = match child {
                    Value::Object(m) => m,
                    _ => unreachable!("all_objects gated the extraction"),
                };
                // LAST, so it is the last column — see contract 2 in the module
                // docs. `insert` on an already-present key keeps the original
                // position, but a row carrying its own `__parent_row_id` is
                // already reserved-namespace abuse and the value must still win.
                c.remove(PARENT_ROW_ID);
                c.insert(PARENT_ROW_ID.to_string(), Value::from(idx));
                let w = &mut kids[pos].writer;
                serde_json::to_writer(&mut *w, &Value::Object(c))
                    .map_err(|e| io_err("child spool write failed", e))?;
                w.write_all(b"\n")
                    .map_err(|e| io_err("child spool write failed", e))?;
            }
        }
        idx += 1;
    }

    pruned_w.flush().map_err(|e| io_err("spool flush failed", e))?;
    drop(pruned_w);

    let pruned_str = pruned_path.to_string_lossy().to_string();
    let csv_path = next_path(dir, ctr, "csv");
    let csv_str = csv_path.to_string_lossy().to_string();
    let schema = sketch(&pruned_str).map_err(|e| format!("{e}"))?;
    let (columns, column_types, item_kind, value_mode, json_cells) = schema.finish();
    let row_count = write_csv_impl(&pruned_str, &csv_str, &columns, json_cells, value_mode)
        .map_err(|e| format!("{e}"))?;

    // Streamed in 64 KiB bites: the point of writing to a file was to keep the
    // whole CSV out of RAM, and slurping it back to hash it would give that up.
    let (size_bytes, content_key) = digest_file(&csv_path)?;

    // The header `write_csv_impl` actually wrote — its own `__row_id` first,
    // then the sketched columns.
    let mut declared = Vec::with_capacity(columns.len() + 1);
    declared.push(crate::ROW_ID_PUB.to_string());
    declared.extend(columns.iter().cloned());

    let mut children = Vec::with_capacity(kids.len());
    for mut kid in kids {
        kid.writer.flush().map_err(|e| io_err("child flush failed", e))?;
        drop(kid.writer);
        children.push(split_level(&kid.jsonl, dir, kid.name, ctr, depth + 1)?);
    }

    Ok(Node {
        name,
        csv_path,
        columns: declared,
        column_types,
        row_count,
        item_kind: item_kind.to_string(),
        size_bytes,
        content_key,
        children,
    })
}

/// Split a JSONL file into the nested-array CSV tree and return its manifest.
///
/// `in_path` is one JSON object per line — the rows of the value being stored
/// (a single-object root is a one-line file). `out_dir` must already exist and
/// should be a caller-owned temp directory: every file written lands there with
/// a generated name.
///
/// Returns the root `Node` as a dict; `children` nests to mirror the artifact
/// tree, so the Python caller uploads depth-first and calls `add_artifact` with
/// each node's `name`.
///
/// Raises `ValueError` rather than degrading, because both callers already have
/// a working Python implementation to fall back to and a HALF-written tree is
/// worse than no tree.
#[pyfunction]
#[pyo3(signature = (in_path, out_dir))]
pub fn jsonl_split_recursive(
    py: Python<'_>,
    in_path: &str,
    out_dir: &str,
) -> PyResult<PyObject> {
    let (in_path, out_dir) = (in_path.to_owned(), out_dir.to_owned());
    let node = py
        .allow_threads(move || {
            let mut ctr = 0usize;
            split_level(Path::new(&in_path), Path::new(&out_dir), String::new(), &mut ctr, 0)
        })
        .map_err(PyValueError::new_err)?;
    Ok(node.to_py(py)?.into_any().unbind())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// No `tempdir` crate in this workspace — the other modules roll their own
    /// off `std::env::temp_dir`, so this does too. Counter-named rather than
    /// tag-named so tests stay independent when run in parallel, and left on
    /// disk after a failure so the spools and CSVs can be read.
    fn tmpdir() -> PathBuf {
        use std::sync::atomic::{AtomicUsize, Ordering};
        static N: AtomicUsize = AtomicUsize::new(0);
        let d = std::env::temp_dir()
            .join(format!("jcs-split-{}", N.fetch_add(1, Ordering::SeqCst)));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    fn spool(dir: &Path, lines: &str) -> PathBuf {
        let p = dir.join("in.jsonl");
        let mut f = File::create(&p).unwrap();
        f.write_all(lines.as_bytes()).unwrap();
        p
    }

    fn run(lines: &str) -> Node {
        let d = tmpdir();
        let inp = spool(&d, lines);
        let mut ctr = 0;
        split_level(&inp, &d, String::new(), &mut ctr, 0).unwrap()
    }

    fn csv_of(n: &Node) -> String {
        std::fs::read_to_string(&n.csv_path).unwrap().replace("\r\n", "\n")
    }

    #[test]
    fn a_flat_row_produces_no_children() {
        let n = run("{\"a\":1,\"b\":\"x\"}\n");
        assert!(n.children.is_empty());
        assert_eq!(n.row_count, 1);
        assert_eq!(csv_of(&n), "__row_id,a,b\n0,1,x\n");
    }

    #[test]
    fn an_array_of_objects_becomes_a_child_keyed_to_the_parent_row() {
        let n = run(
            "{\"id\":1,\"items\":[{\"s\":\"a\"},{\"s\":\"b\"}]}\n\
             {\"id\":2,\"items\":[{\"s\":\"c\"}]}\n",
        );
        assert_eq!(n.children.len(), 1);
        let kid = &n.children[0];
        assert_eq!(kid.name, "items");
        // `items` is gone from the parent; `__row_id` is 0-based and written by
        // write_csv_impl, not stamped here.
        assert_eq!(csv_of(&n), "__row_id,id\n0,1\n1,2\n");
        // `__parent_row_id` is LAST, and points at the parent's __row_id.
        assert_eq!(
            csv_of(kid),
            "__row_id,s,__parent_row_id\n0,a,0\n1,b,0\n2,c,1\n"
        );
    }

    #[test]
    fn nesting_recurses_and_names_are_relative_to_the_parent() {
        let n = run("{\"o\":[{\"id\":9,\"li\":[{\"q\":2}]}]}\n");
        assert_eq!(n.children.len(), 1);
        let o = &n.children[0];
        assert_eq!(o.name, "o");
        assert_eq!(o.children.len(), 1);
        // "li", not "o.li" — add_artifact stores the level-relative path.
        assert_eq!(o.children[0].name, "li");
        assert_eq!(csv_of(&o.children[0]), "__row_id,q,__parent_row_id\n0,2,0\n");
    }

    #[test]
    fn an_array_inside_a_plain_object_is_named_by_its_dotted_path() {
        let n = run("{\"order\":{\"ref\":\"R1\",\"lines\":[{\"q\":1}]}}\n");
        assert_eq!(n.children.len(), 1);
        assert_eq!(n.children[0].name, "order.lines");
        // the object's scalars stay in the parent, flattened
        assert_eq!(csv_of(&n), "__row_id,order.ref\n0,R1\n");
    }

    #[test]
    fn only_all_object_arrays_are_extracted() {
        // empty, scalar and mixed arrays stay inline as JSON cells — this is
        // what makes the split lossless.
        let n = run("{\"e\":[],\"s\":[1,2],\"m\":[{\"a\":1},7]}\n");
        assert!(n.children.is_empty(), "nothing should have been extracted");
        let csv = csv_of(&n);
        assert!(csv.contains("[]"), "{csv}");
        assert!(csv.contains("[1,2]"), "{csv}");
    }

    #[test]
    fn a_nested_file_ref_is_left_whole() {
        let n = run(
            "{\"f\":{\"__file_ref\":true,\"file_id\":\"x\",\"rows\":[{\"a\":1}]}}\n",
        );
        // F1: the ref is one opaque JSON cell, so its inner `rows` is NOT a
        // table. Extracting it would rewrite somebody else's pointer.
        assert!(n.children.is_empty());
        assert!(csv_of(&n).contains("__file_ref"));
    }

    #[test]
    fn a_marker_that_is_not_literally_true_is_ordinary_data() {
        // data_reference.is_file_ref is `is True`, not "key present".
        let n = run("{\"f\":{\"__file_ref\":\"no\",\"rows\":[{\"a\":1}]}}\n");
        assert_eq!(n.children.len(), 1);
        assert_eq!(n.children[0].name, "f.rows");
    }

    #[test]
    fn a_sparse_array_field_still_joins_by_parent_row() {
        // row 1 has no `items` at all — its children must not shift.
        let n = run(
            "{\"id\":1,\"items\":[{\"s\":\"a\"}]}\n\
             {\"id\":2}\n\
             {\"id\":3,\"items\":[{\"s\":\"c\"}]}\n",
        );
        assert_eq!(
            csv_of(&n.children[0]),
            "__row_id,s,__parent_row_id\n0,a,0\n1,c,2\n"
        );
    }

    #[test]
    fn an_empty_input_is_a_zero_row_table_not_an_error() {
        let n = run("");
        assert_eq!(n.row_count, 0);
        assert!(n.children.is_empty());
    }

    #[test]
    fn a_row_supplying_its_own_parent_row_id_does_not_get_two_columns() {
        let n = run("{\"items\":[{\"__parent_row_id\":99,\"s\":\"a\"}]}\n");
        let csv = csv_of(&n.children[0]);
        assert_eq!(csv.matches("__parent_row_id").count(), 1, "{csv}");
        // and OUR value wins, or the join is decided by the data
        assert!(csv.ends_with(",0\n"), "{csv}");
    }

    #[test]
    fn depth_beyond_the_cap_stays_inline_rather_than_failing() {
        // MAX_SPLIT_DEPTH levels of `a:[{...}]`, then one more.
        let mut s = String::new();
        for _ in 0..=MAX_SPLIT_DEPTH {
            s.push_str("{\"a\":[");
        }
        s.push_str("{\"leaf\":1}");
        for _ in 0..=MAX_SPLIT_DEPTH {
            s.push_str("]}");
        }
        s.push('\n');
        let d = tmpdir();
        let inp = spool(&d, &s);
        let mut ctr = 0;
        let n = split_level(&inp, &d, String::new(), &mut ctr, 0)
            .expect("past the cap the array stays inline; it must not error");
        // walk to the deepest split level
        let mut cur = &n;
        let mut levels = 0;
        while let Some(k) = cur.children.first() {
            cur = k;
            levels += 1;
        }
        assert_eq!(levels, MAX_SPLIT_DEPTH);
        assert!(csv_of(cur).contains("leaf"), "the tail must survive inline");
    }

    #[test]
    fn a_non_object_row_is_refused_rather_than_guessed() {
        let d = tmpdir();
        let inp = spool(&d, "{\"a\":1}\n[1,2]\n");
        let mut ctr = 0;
        let err = split_level(&inp, &d, String::new(), &mut ctr, 0).unwrap_err();
        assert!(err.contains("an array"), "{err}");
    }
}
