//! SA-1905 — the TREE sketch: what shape does this JSONL file actually have?
//!
//! `Schema` ([`crate::Schema`]) flattens on observation: `flatten_object` turns
//! `{"o":{"id":1}}` into the column `o.id` and the nesting is gone before
//! anything can look at it. That is exactly right for CSV, which has no nested
//! types — and it is why the control plane has to reconstruct nesting by
//! splitting arrays into sibling CSV files joined on `__parent_row_id`.
//!
//! This module observes the same lines a second time without flattening, so the
//! Arrow writer can emit `List<Struct<…>>` and the reader can hand back the
//! original value with no join at all.
//!
//! # Null is neutral
//!
//! `Value::Null` sets one flag and locks nothing. That generalises the flat
//! model's "first non-null observation wins" ([`crate::Schema::observe`]) from
//! leaves to whole subtrees, and is what makes a sparse array MERGE rather than
//! conflict: `[{"a":[]},{"a":[{"x":1}]}]` observes `saw_array` once and folds
//! the one element into one node, giving `List<Struct<x:Int64>>` with a valid,
//! length-0 list on row 0. CSV writes `"[]"` and `""` for two different things
//! and then hopes `column_types` survives to tell them apart.
//!
//! # Heterogeneity is decided by the WRITER, once
//!
//! When a path is sometimes an object and sometimes a scalar there is no honest
//! nested type, so the node becomes [`Shape::Json`] — one deterministic choice
//! made at schema time. That is the SA-1984 fix: today `_resolve_collisions`
//! (`csv_converter.py:71-120`) has to arbitrate a leaf column against branch
//! columns per row, from cell emptiness, and its own comment records that
//! "column order alone decided crash vs data loss".
//!
//! Heterogeneity among *scalars* is different and must stay different:
//! [`Shape::Token`] keeps the raw text, because JSON-encoding a mixed scalar
//! column would turn `"N/A"` into `"\"N/A\""`.

use serde_json::{Map, Value};
use std::collections::HashMap;

use super::{NumInfo, K_BOOLEAN, K_NUMBER, K_STRING, NUM_FLOAT, NUM_INT};

/// The `__file_ref` discriminator. MUST equal `data_reference.FILE_REF_MARKER`
/// on the control plane; asserted from Python via the exported constant.
///
/// A nested file_ref is a POINTER, not data — `_flatten` (`csv_converter.py:51-60`)
/// already refuses to recurse into one, because doing so once caused a
/// "22-column explosion". Recursing into it HERE would be worse: it would mint
/// a `Struct` of the pointer's own bookkeeping fields.
pub(crate) const FILE_REF_MARKER: &str = "__file_ref";

/// Past this many distinct keys a JSON object is being used as a MAP, not a
/// record. A `Struct` would then allocate one array per key per batch.
pub(crate) const MAX_STRUCT_FIELDS: usize = 1024;

/// Past this depth the shape is almost certainly recursive/unbounded.
pub(crate) const MAX_DEPTH: u16 = 16;

/// The resolved shape of one path. Deliberately independent of `arrow` so the
/// observation logic stays testable without building a single array.
#[derive(Debug, Clone, PartialEq)]
pub(crate) enum Shape {
    /// Every observation was null (or there were none). Zero-width; reads None.
    Null,
    Bool,
    Int,
    Float,
    /// A number no fixed-width type holds exactly (> i64). Stored as its literal
    /// text and read back as a Python int — arbitrary precision, like the CSV.
    NumberToken,
    Str,
    /// More than one scalar kind at this path. Stored as the raw token; the
    /// payload is the leaf kind the CSV would have declared, so a strict-parity
    /// reader can reproduce `_cast_value` exactly.
    Token(&'static str),
    /// Structurally heterogeneous, opaque (`__file_ref`), or past a guard.
    /// Stored as `serde_json` of the whole subtree.
    Json,
    /// Field order is first-seen, matching `preserve_order` on the CSV side.
    Struct(Vec<(String, Shape)>),
    List(Box<Shape>),
}

impl Shape {
    /// The `smdg.encoding` metadata value a field of this shape carries.
    /// `native` is implicit and never written.
    pub(crate) fn encoding(&self) -> Option<&'static str> {
        match self {
            Shape::Json => Some("json"),
            Shape::NumberToken => Some("number_token"),
            Shape::Token(_) => Some("token"),
            _ => None,
        }
    }

    /// For [`Shape::Token`], the leaf kind the CSV `column_types` map would
    /// carry — the argument a strict-parity reader passes to `_cast_value`.
    pub(crate) fn declared(&self) -> Option<&'static str> {
        match self {
            Shape::Token(k) => Some(k),
            _ => None,
        }
    }
}

/// What has been seen at one path, across every line of the file.
#[derive(Default)]
pub(crate) struct ObservedNode {
    saw_object: bool,
    saw_array: bool,
    saw_string: bool,
    saw_bool: bool,
    saw_number: bool,
    saw_null: bool,
    /// Set once: the leaf kind of the FIRST non-null scalar, mirroring
    /// `_infer_into`'s first-wins rule so `Token`'s payload matches what the
    /// CSV twin declared for the same path.
    first_leaf: Option<&'static str>,
    /// Forced to [`Shape::Json`] regardless of what else was seen.
    opaque: bool,
    num: NumInfo,
    /// Insertion order, and an index so a wide record does not cost O(fields)
    /// per key per row.
    children: Vec<(String, ObservedNode)>,
    index: HashMap<String, usize>,
    /// The single node every array element folds into.
    element: Option<Box<ObservedNode>>,
}

impl ObservedNode {
    pub(crate) fn new() -> Self {
        Self::default()
    }

    /// Fold one value in at this path.
    pub(crate) fn observe(&mut self, v: &Value, depth: u16) {
        if self.opaque {
            return;
        }
        match v {
            // Neutral: locks nothing, so a later row still decides the shape.
            Value::Null => self.saw_null = true,
            Value::Bool(_) => {
                self.saw_bool = true;
                self.first_leaf.get_or_insert(K_BOOLEAN);
            }
            Value::Number(n) => {
                self.saw_number = true;
                self.first_leaf.get_or_insert(K_NUMBER);
                self.num.observe(&n.to_string());
            }
            Value::String(_) => {
                self.saw_string = true;
                self.first_leaf.get_or_insert(K_STRING);
            }
            Value::Array(items) => {
                self.saw_array = true;
                if depth >= MAX_DEPTH {
                    self.opaque = true;
                    return;
                }
                let el = self
                    .element
                    .get_or_insert_with(|| Box::new(ObservedNode::new()));
                for it in items {
                    el.observe(it, depth + 1);
                }
            }
            Value::Object(m) => {
                if m.contains_key(FILE_REF_MARKER) {
                    self.opaque = true;
                    return;
                }
                self.saw_object = true;
                if depth >= MAX_DEPTH {
                    self.opaque = true;
                    return;
                }
                self.observe_fields(m, depth);
            }
        }
    }

    fn observe_fields(&mut self, m: &Map<String, Value>, depth: u16) {
        for (k, val) in m {
            let idx = match self.index.get(k) {
                Some(&i) => i,
                None => {
                    if self.children.len() >= MAX_STRUCT_FIELDS {
                        // Used as a map, not a record. Collapse the whole node.
                        self.opaque = true;
                        self.children.clear();
                        self.index.clear();
                        return;
                    }
                    let i = self.children.len();
                    self.index.insert(k.clone(), i);
                    self.children.push((k.clone(), ObservedNode::new()));
                    i
                }
            };
            self.children[idx].1.observe(val, depth + 1);
        }
    }

    /// Does EVERY line qualify as a record?
    ///
    /// The ROOT's value-mode decision must mirror
    /// ``DataStoreService._is_value_root`` — "a list with ANY non-dict element"
    /// — and `null` is not a dict.
    ///
    /// Null is neutral everywhere else in this lattice, deliberately: a null
    /// FIELD must not decide its column's type. But a null LINE is a value in
    /// its own right, and treating it as neutral here turns
    /// `[{"id":1}, null, {"id":3}]` into `[{"id":1}, {"id":null}, {"id":3}]` —
    /// a RecordBatch has no row-level validity, so a null row becomes a row of
    /// nulls. That sparse array is precisely the shape the SA-1977 comment is
    /// written about, broken in a new way, and the CSV path gets it right.
    pub(crate) fn root_is_all_objects(&self) -> bool {
        self.saw_object
            && !self.opaque
            && !(self.saw_null
                || self.saw_array
                || self.saw_string
                || self.saw_bool
                || self.saw_number)
    }

    /// How many of the three STRUCTURAL categories were seen. More than one and
    /// there is no honest nested type for this path.
    fn structural_kinds(&self) -> u8 {
        u8::from(self.saw_object)
            + u8::from(self.saw_array)
            + u8::from(self.saw_string || self.saw_bool || self.saw_number)
    }

    fn scalar_kinds(&self) -> u8 {
        u8::from(self.saw_string) + u8::from(self.saw_bool) + u8::from(self.saw_number)
    }

    /// Resolve bottom-up. Pure; safe to call more than once.
    pub(crate) fn resolve(&self) -> Shape {
        if self.opaque || self.structural_kinds() > 1 {
            return Shape::Json;
        }
        if self.saw_object {
            return Shape::Struct(
                self.children
                    .iter()
                    .map(|(k, n)| (k.clone(), n.resolve()))
                    .collect(),
            );
        }
        if self.saw_array {
            let el = match &self.element {
                Some(e) => e.resolve(),
                // `[]` on every row: nothing to observe, and a zero-width
                // element type still round-trips an empty list exactly.
                None => Shape::Null,
            };
            return Shape::List(Box::new(el));
        }
        if self.scalar_kinds() > 1 {
            // Mixed scalars. `first_leaf` is what the CSV declared for this
            // path, so a strict-parity reader can reproduce `_cast_value`.
            return Shape::Token(self.first_leaf.unwrap_or(K_STRING));
        }
        if self.saw_string {
            return Shape::Str;
        }
        if self.saw_bool {
            return Shape::Bool;
        }
        if self.saw_number {
            // Deliberately NOT `NumInfo::hint()`, which the CSV path uses.
            //
            // `hint()` answers "what fixed-width type holds these digits", and
            // for a column of `[1, 1.5]` that is `Float64`. But `_cast_value`
            // tries `int()` before `float()`, so CSV reads `1` back as an `int`
            // and `1.0` as a `float` — a distinction `Float64` erases, and one
            // no reader could recover afterwards. Keeping the literal costs
            // text for exactly the mixed columns and is exact for all of them.
            return match self.num.hint() {
                NUM_INT => Shape::Int,
                NUM_FLOAT if self.num.saw_int => Shape::NumberToken,
                NUM_FLOAT => Shape::Float,
                _ => Shape::NumberToken,
            };
        }
        // Only nulls, or nothing at all.
        Shape::Null
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shape_of(lines: &[&str]) -> Shape {
        let mut n = ObservedNode::new();
        for l in lines {
            n.observe(&serde_json::from_str::<Value>(l).unwrap(), 0);
        }
        n.resolve()
    }

    fn field<'a>(s: &'a Shape, name: &str) -> &'a Shape {
        match s {
            Shape::Struct(fs) => &fs.iter().find(|(k, _)| k == name).unwrap().1,
            other => panic!("not a struct: {other:?}"),
        }
    }

    // ---- scalars ---------------------------------------------------------

    #[test]
    fn scalar_shapes() {
        assert_eq!(shape_of(&["1", "2"]), Shape::Int);
        // integers BESIDE fractions keep their literals — `Float64` would erase
        // the int/float distinction `_cast_value` preserves
        assert_eq!(shape_of(&["1", "1.5"]), Shape::NumberToken);
        // fractions only: `Float64` is exact and `_cast_value` agrees
        assert_eq!(shape_of(&["1.5", "2.5"]), Shape::Float);
        assert_eq!(shape_of(&["\"a\""]), Shape::Str);
        assert_eq!(shape_of(&["true", "false"]), Shape::Bool);
        assert_eq!(shape_of(&["null", "null"]), Shape::Null);
        assert_eq!(shape_of(&[]), Shape::Null);
        // 2^53+1 and beyond keep their digits as an exact literal
        assert_eq!(shape_of(&["9007199254740993"]), Shape::Int);
        assert_eq!(
            shape_of(&["10000000000000000000000000"]),
            Shape::NumberToken
        );
    }

    /// A null must not decide the shape — the whole point of the lattice.
    #[test]
    fn null_is_neutral_at_every_level() {
        assert_eq!(shape_of(&["null", "1", "null"]), Shape::Int);
        assert_eq!(shape_of(&["null", "\"a\""]), Shape::Str);
        assert_eq!(
            shape_of(&["{\"a\":null}", "{\"a\":1}"]),
            Shape::Struct(vec![("a".into(), Shape::Int)])
        );
        assert_eq!(
            shape_of(&["[null]", "[1]"]),
            Shape::List(Box::new(Shape::Int))
        );
    }

    /// SA-1984, the half CSV cannot express at all: a path that is sometimes
    /// absent-as-null and sometimes an object is simply a nullable Struct.
    #[test]
    fn null_beside_an_object_is_a_struct_not_a_collision() {
        assert_eq!(
            shape_of(&["{\"x\":null}", "{\"x\":{\"a\":1}}"]),
            Shape::Struct(vec![(
                "x".into(),
                Shape::Struct(vec![("a".into(), Shape::Int)])
            )])
        );
    }

    // ---- heterogeneity ---------------------------------------------------

    #[test]
    fn mixed_scalars_keep_the_raw_token_and_the_declared_kind() {
        let s = shape_of(&["{\"n\":5}", "{\"n\":\"N/A\"}"]);
        assert_eq!(field(&s, "n"), &Shape::Token(K_NUMBER));
        assert_eq!(field(&s, "n").declared(), Some(K_NUMBER));
        assert_eq!(field(&s, "n").encoding(), Some("token"));
        // first-wins, exactly like `_infer_into`
        let t = shape_of(&["{\"n\":\"N/A\"}", "{\"n\":5}"]);
        assert_eq!(field(&t, "n"), &Shape::Token(K_STRING));
    }

    /// SA-1984, the hard half: both sides non-empty, where the CSV reader has
    /// no signal and loses one of them.
    #[test]
    fn scalar_beside_a_structure_is_json_encoded() {
        assert_eq!(field(&shape_of(&["{\"x\":5}", "{\"x\":{\"a\":1}}"]), "x"), &Shape::Json);
        assert_eq!(field(&shape_of(&["{\"x\":5}", "{\"x\":[1]}"]), "x"), &Shape::Json);
        assert_eq!(
            field(&shape_of(&["{\"x\":[1]}", "{\"x\":{\"a\":1}}"]), "x"),
            &Shape::Json
        );
    }

    // ---- nesting ---------------------------------------------------------

    #[test]
    fn the_recursive_split_shape_resolves_to_one_nested_type() {
        // the exact payload that becomes 3 CSV files today
        let s = shape_of(&["{\"orders\":[{\"id\":1,\"items\":[{\"sku\":\"A\"}]}]}"]);
        assert_eq!(
            s,
            Shape::Struct(vec![(
                "orders".into(),
                Shape::List(Box::new(Shape::Struct(vec![
                    ("id".into(), Shape::Int),
                    (
                        "items".into(),
                        Shape::List(Box::new(Shape::Struct(vec![("sku".into(), Shape::Str)])))
                    ),
                ])))
            )])
        );
    }

    /// A sparse array merges instead of conflicting, and an all-empty array is
    /// still a list (of nothing) rather than a guess.
    #[test]
    fn sparse_and_empty_arrays_merge() {
        assert_eq!(
            field(&shape_of(&["{\"a\":[]}", "{\"a\":[{\"x\":1}]}"]), "a"),
            &Shape::List(Box::new(Shape::Struct(vec![("x".into(), Shape::Int)])))
        );
        assert_eq!(
            field(&shape_of(&["{\"a\":[]}", "{\"a\":[]}"]), "a"),
            &Shape::List(Box::new(Shape::Null))
        );
    }

    #[test]
    fn struct_fields_keep_first_seen_order() {
        let s = shape_of(&["{\"z\":1,\"a\":2}", "{\"m\":3,\"z\":4}"]);
        match s {
            Shape::Struct(fs) => {
                let keys: Vec<&str> = fs.iter().map(|(k, _)| k.as_str()).collect();
                assert_eq!(keys, vec!["z", "a", "m"]);
            }
            other => panic!("{other:?}"),
        }
    }

    // ---- guards ----------------------------------------------------------

    /// A nested file_ref is a pointer. Recursing into it would mint a Struct of
    /// its bookkeeping fields; `_flatten:51-60` refuses for the same reason.
    #[test]
    fn a_nested_file_ref_is_opaque() {
        let s = shape_of(&["{\"out\":{\"__file_ref\":true,\"file_id\":\"f1\",\"columns\":[]}}"]);
        assert_eq!(field(&s, "out"), &Shape::Json);
        assert_eq!(field(&s, "out").encoding(), Some("json"));
    }

    #[test]
    fn a_map_like_object_collapses_instead_of_exploding() {
        let wide: String = (0..MAX_STRUCT_FIELDS + 5)
            .map(|i| format!("\"k{i}\":{i}"))
            .collect::<Vec<_>>()
            .join(",");
        assert_eq!(shape_of(&[&format!("{{{wide}}}")]), Shape::Json);
    }

    #[test]
    fn past_max_depth_the_node_collapses() {
        let mut deep = String::from("1");
        for _ in 0..(MAX_DEPTH + 2) {
            deep = format!("[{deep}]");
        }
        // somewhere down the spine it becomes Json rather than nesting forever
        let mut s = &shape_of(&[&deep]);
        let mut hops = 0;
        while let Shape::List(inner) = s {
            s = inner;
            hops += 1;
        }
        assert!(hops <= MAX_DEPTH as usize + 1, "nested {hops} deep");
        assert_eq!(s, &Shape::Json);
    }
}
