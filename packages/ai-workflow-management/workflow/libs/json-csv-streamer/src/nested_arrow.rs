//! SA-1905 — writing a [`Shape`] tree as nested Arrow.
//!
//! [`crate::nested`] decides WHAT the file is; this decides how to build it.
//! One [`NodeBuilder`] per schema node, appended once per row, flushed per
//! batch — the same bounded-RAM discipline the CSV writer has.
//!
//! # The invariant that makes nulls work
//!
//! **A null Struct must still advance every descendant by one slot.** Arrow
//! stores a struct's children as full-length sibling arrays plus one validity
//! bitmap; a child that skipped a row would leave every subsequent row of that
//! child misaligned against its parent — silent, and only visible as wrong data
//! several fields away. `StructArray::try_new` rejects a length mismatch, which
//! is a backstop, not a substitute for
//! `every_builder_advances_exactly_one_slot_per_row`.
//!
//! # The two-line fix for empty-vs-missing
//!
//! For a list, a NULL list and an EMPTY list push the **same** offset and differ
//! only in the validity bit. That distinction is the whole of SA-1988
//! (`csv_converter.py:275-282` — *"a missing field and a genuine empty string
//! write the SAME empty cell … it is a **format** that cannot represent the
//! difference"*), and it costs one bit.

use std::sync::Arc;

use arrow::array::{
    ArrayBuilder, ArrayRef, BooleanBuilder, Float64Builder, Int64Builder, ListArray, NullArray,
    StringBuilder, StructArray,
};
use arrow::buffer::{NullBuffer, OffsetBuffer, ScalarBuffer};
use arrow::datatypes::{DataType, Field, FieldRef, Fields};
use serde_json::Value;

use super::nested::Shape;

/// The metadata key carrying [`Shape::encoding`]. Round-trips through IPC, so
/// the reader is told what the writer decided instead of re-sniffing it — the
/// rule `lib.rs` already states: re-deriving a fact the producer computed is
/// the bug this crate exists to remove.
pub(crate) const META_ENCODING: &str = "smdg.encoding";
/// For `token` fields, the leaf kind the CSV `column_types` would declare, so a
/// strict-parity reader can reproduce `_cast_value` exactly.
pub(crate) const META_DECLARED: &str = "smdg.declared";

/// The physical type for a shape. Everything that is not natively typed lands
/// in `Utf8` and says so in its field metadata.
pub(crate) fn data_type(shape: &Shape) -> DataType {
    match shape {
        Shape::Null => DataType::Null,
        Shape::Bool => DataType::Boolean,
        Shape::Int => DataType::Int64,
        Shape::Float => DataType::Float64,
        Shape::NumberToken | Shape::Str | Shape::Token(_) | Shape::Json => DataType::Utf8,
        Shape::Struct(fs) => DataType::Struct(struct_fields(fs)),
        Shape::List(el) => DataType::List(list_field(el)),
    }
}

pub(crate) fn field_for(name: &str, shape: &Shape) -> FieldRef {
    // Every field is nullable: a value absent on one row is the normal case,
    // and a non-nullable field would make the writer reject ordinary data.
    let mut f = Field::new(name, data_type(shape), true);
    let mut meta = std::collections::HashMap::new();
    if let Some(e) = shape.encoding() {
        meta.insert(META_ENCODING.to_string(), e.to_string());
    }
    if let Some(d) = shape.declared() {
        meta.insert(META_DECLARED.to_string(), d.to_string());
    }
    if !meta.is_empty() {
        f = f.with_metadata(meta);
    }
    Arc::new(f)
}

fn struct_fields(fs: &[(String, Shape)]) -> Fields {
    fs.iter()
        .map(|(k, s)| field_for(k, s))
        .collect::<Vec<_>>()
        .into()
}

/// `"item"` is the arrow-rs convention for a list's element field.
fn list_field(el: &Shape) -> FieldRef {
    field_for("item", el)
}

/// The CSV cell text for a scalar — byte-identical to what `flatten_object` /
/// `value_cell` write, so a `token` column and its CSV twin hold the same bytes.
fn token_text(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        Value::Bool(b) => b.to_string(),
        other => other.to_string(),
    }
}

enum StrKind {
    /// A real string; store it verbatim.
    Plain,
    /// An exact numeric literal too wide for i64.
    NumberToken,
    /// A heterogeneous scalar column, stored as the CSV cell text.
    Token,
    /// A structurally heterogeneous / opaque subtree, stored as JSON.
    Json,
}

/// One builder per schema node. `append` takes `Option<&Value>` where `None`
/// means "this row had no value here", which is how a null parent propagates.
pub(crate) enum NodeBuilder {
    Null {
        len: usize,
    },
    Bool(BooleanBuilder),
    Int(Int64Builder),
    Float(Float64Builder),
    Str {
        b: StringBuilder,
        kind: StrKind,
    },
    Struct {
        fields: Fields,
        children: Vec<NodeBuilder>,
        validity: Vec<bool>,
    },
    List {
        field: FieldRef,
        child: Box<NodeBuilder>,
        offsets: Vec<i32>,
        validity: Vec<bool>,
    },
}

impl NodeBuilder {
    pub(crate) fn new(shape: &Shape) -> Self {
        match shape {
            Shape::Null => NodeBuilder::Null { len: 0 },
            Shape::Bool => NodeBuilder::Bool(BooleanBuilder::new()),
            Shape::Int => NodeBuilder::Int(Int64Builder::new()),
            Shape::Float => NodeBuilder::Float(Float64Builder::new()),
            Shape::Str => NodeBuilder::Str {
                b: StringBuilder::new(),
                kind: StrKind::Plain,
            },
            Shape::NumberToken => NodeBuilder::Str {
                b: StringBuilder::new(),
                kind: StrKind::NumberToken,
            },
            Shape::Token(_) => NodeBuilder::Str {
                b: StringBuilder::new(),
                kind: StrKind::Token,
            },
            Shape::Json => NodeBuilder::Str {
                b: StringBuilder::new(),
                kind: StrKind::Json,
            },
            Shape::Struct(fs) => NodeBuilder::Struct {
                fields: struct_fields(fs),
                children: fs.iter().map(|(_, s)| NodeBuilder::new(s)).collect(),
                validity: Vec::new(),
            },
            Shape::List(el) => NodeBuilder::List {
                field: list_field(el),
                child: Box::new(NodeBuilder::new(el)),
                // A list's offsets always start at 0 and carry len+1 entries.
                offsets: vec![0],
                validity: Vec::new(),
            },
        }
    }

    /// Rows appended so far. Every builder must report the same number after
    /// each row, at every depth.
    pub(crate) fn len(&self) -> usize {
        match self {
            NodeBuilder::Null { len } => *len,
            NodeBuilder::Bool(b) => b.len(),
            NodeBuilder::Int(b) => b.len(),
            NodeBuilder::Float(b) => b.len(),
            NodeBuilder::Str { b, .. } => b.len(),
            NodeBuilder::Struct { validity, .. } => validity.len(),
            NodeBuilder::List { validity, .. } => validity.len(),
        }
    }

    /// Append exactly one slot. `None` (or a value of the wrong kind, which the
    /// sketch says cannot happen but which must not corrupt the file if it
    /// does) appends a null.
    pub(crate) fn append(&mut self, v: Option<&Value>) {
        let v = match v {
            Some(Value::Null) | None => None,
            Some(x) => Some(x),
        };
        match self {
            NodeBuilder::Null { len } => *len += 1,
            NodeBuilder::Bool(b) => b.append_option(v.and_then(Value::as_bool)),
            NodeBuilder::Int(b) => b.append_option(v.and_then(|x| match x {
                Value::Number(n) => n.to_string().parse::<i64>().ok(),
                _ => None,
            })),
            NodeBuilder::Float(b) => b.append_option(v.and_then(|x| match x {
                Value::Number(n) => n.to_string().parse::<f64>().ok(),
                _ => None,
            })),
            NodeBuilder::Str { b, kind } => match v {
                None => b.append_null(),
                Some(x) => match kind {
                    StrKind::Plain => match x.as_str() {
                        Some(s) => b.append_value(s),
                        None => b.append_null(),
                    },
                    StrKind::NumberToken | StrKind::Token => b.append_value(token_text(x)),
                    StrKind::Json => b.append_value(x.to_string()),
                },
            },
            NodeBuilder::Struct {
                fields,
                children,
                validity,
            } => {
                let obj = v.and_then(Value::as_object);
                validity.push(obj.is_some());
                // EVERY child advances, valid parent or not. See the module docs.
                for (i, child) in children.iter_mut().enumerate() {
                    let name = fields[i].name();
                    child.append(obj.and_then(|m| m.get(name)));
                }
            }
            NodeBuilder::List {
                child,
                offsets,
                validity,
                ..
            } => {
                match v.and_then(Value::as_array) {
                    Some(items) => {
                        for it in items {
                            child.append(Some(it));
                        }
                        validity.push(true);
                    }
                    // A null list and an empty list push the SAME offset and
                    // differ only here. That is the whole of SA-1988.
                    None => validity.push(false),
                }
                offsets.push(child.len() as i32);
            }
        }
    }

    /// Consume the accumulated rows into an array and reset for the next batch.
    pub(crate) fn finish(&mut self) -> ArrayRef {
        match self {
            NodeBuilder::Null { len } => {
                let a = Arc::new(NullArray::new(*len)) as ArrayRef;
                *len = 0;
                a
            }
            NodeBuilder::Bool(b) => Arc::new(b.finish()) as ArrayRef,
            NodeBuilder::Int(b) => Arc::new(b.finish()) as ArrayRef,
            NodeBuilder::Float(b) => Arc::new(b.finish()) as ArrayRef,
            NodeBuilder::Str { b, .. } => Arc::new(b.finish()) as ArrayRef,
            NodeBuilder::Struct {
                fields,
                children,
                validity,
            } => {
                let arrays: Vec<ArrayRef> = children.iter_mut().map(|c| c.finish()).collect();
                let nulls = nulls_from(validity);
                let len = validity.len();
                validity.clear();
                // A struct with no fields still has a length; `try_new` cannot
                // infer it, so build it explicitly.
                let a: ArrayRef = if fields.is_empty() {
                    Arc::new(StructArray::new_empty_fields(len, nulls))
                } else {
                    Arc::new(StructArray::try_new(fields.clone(), arrays, nulls).expect(
                        "struct child length mismatch — a builder skipped a row",
                    ))
                };
                a
            }
            NodeBuilder::List {
                field,
                child,
                offsets,
                validity,
            } => {
                let values = child.finish();
                let nulls = nulls_from(validity);
                let offs = OffsetBuffer::new(ScalarBuffer::from(std::mem::replace(
                    offsets,
                    vec![0],
                )));
                validity.clear();
                Arc::new(
                    ListArray::try_new(field.clone(), offs, values, nulls)
                        .expect("list offsets/values mismatch"),
                ) as ArrayRef
            }
        }
    }
}

fn nulls_from(validity: &[bool]) -> Option<NullBuffer> {
    if validity.iter().all(|v| *v) {
        None
    } else {
        Some(NullBuffer::from(validity.to_vec()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::nested::ObservedNode;
    use arrow::array::Array;

    fn shape_of(lines: &[&str]) -> Shape {
        let mut n = ObservedNode::new();
        for l in lines {
            n.observe(&serde_json::from_str::<Value>(l).unwrap(), 0);
        }
        n.resolve()
    }

    fn build(lines: &[&str]) -> ArrayRef {
        let shape = shape_of(lines);
        let mut b = NodeBuilder::new(&shape);
        for l in lines {
            let v: Value = serde_json::from_str(l).unwrap();
            b.append(Some(&v));
        }
        b.finish()
    }

    /// The invariant the whole module rests on, checked at EVERY node after
    /// EVERY row — including under a null parent, which is the case that
    /// silently misaligns children if it is got wrong.
    fn assert_lengths(b: &NodeBuilder, expect: usize, path: &str) {
        assert_eq!(b.len(), expect, "length at {path}");
        match b {
            NodeBuilder::Struct {
                fields, children, ..
            } => {
                for (i, c) in children.iter().enumerate() {
                    assert_lengths(c, expect, &format!("{path}.{}", fields[i].name()));
                }
            }
            // A list's child advances by the number of ELEMENTS, not rows, so
            // its length is checked against the last offset instead.
            NodeBuilder::List { child, offsets, .. } => {
                assert_eq!(
                    child.len(),
                    *offsets.last().unwrap() as usize,
                    "list child vs offsets at {path}"
                );
            }
            _ => {}
        }
    }

    #[test]
    fn every_builder_advances_exactly_one_slot_per_row() {
        let lines = [
            "{\"a\":1,\"o\":{\"x\":1,\"y\":[1,2]}}",
            "{\"a\":2}",                            // o absent -> null struct
            "{\"a\":3,\"o\":null}",                 // o explicitly null
            "{\"a\":4,\"o\":{\"y\":[]}}",           // x absent, y empty
            "{\"a\":5,\"o\":{\"x\":9,\"y\":null}}", // y null
        ];
        let shape = shape_of(&lines);
        let mut b = NodeBuilder::new(&shape);
        for (i, l) in lines.iter().enumerate() {
            b.append(Some(&serde_json::from_str::<Value>(l).unwrap()));
            assert_lengths(&b, i + 1, "root");
        }
        // and it survives the round into arrow, which validates lengths itself
        let arr = b.finish();
        assert_eq!(arr.len(), lines.len());
    }

    #[test]
    fn a_null_struct_is_null_not_a_struct_of_nulls() {
        let arr = build(&["{\"o\":{\"x\":1}}", "{\"o\":null}"]);
        let root = arr.as_any().downcast_ref::<StructArray>().unwrap();
        let o = root.column_by_name("o").unwrap();
        assert!(o.is_valid(0));
        assert!(o.is_null(1));
    }

    /// SA-1988, the two-line difference.
    #[test]
    fn an_empty_list_is_valid_and_a_null_list_is_not() {
        let arr = build(&["{\"a\":[1]}", "{\"a\":[]}", "{\"a\":null}"]);
        let root = arr.as_any().downcast_ref::<StructArray>().unwrap();
        let a = root
            .column_by_name("a")
            .unwrap()
            .as_any()
            .downcast_ref::<ListArray>()
            .unwrap();
        assert!(a.is_valid(0) && a.value_length(0) == 1);
        assert!(a.is_valid(1) && a.value_length(1) == 0, "empty list is VALID");
        assert!(a.is_null(2), "null list is NOT valid");
    }

    #[test]
    fn types_follow_the_shape() {
        assert_eq!(build(&["1", "2"]).data_type(), &DataType::Int64);
        assert_eq!(build(&["1.5", "2.5"]).data_type(), &DataType::Float64);
        // mixed int/fraction keeps the literal, so it is Utf8 not Float64
        assert_eq!(build(&["1", "1.5"]).data_type(), &DataType::Utf8);
        assert_eq!(build(&["true"]).data_type(), &DataType::Boolean);
        assert_eq!(build(&["\"a\""]).data_type(), &DataType::Utf8);
        assert_eq!(build(&["null"]).data_type(), &DataType::Null);
        assert_eq!(
            build(&["10000000000000000000000000"]).data_type(),
            &DataType::Utf8
        );
    }

    #[test]
    fn a_token_column_holds_the_same_bytes_as_its_csv_cell() {
        let arr = build(&["{\"n\":5}", "{\"n\":\"N/A\"}", "{\"n\":true}"]);
        let root = arr.as_any().downcast_ref::<StructArray>().unwrap();
        let n = root.column_by_name("n").unwrap();
        let s = n.as_any().downcast_ref::<arrow::array::StringArray>().unwrap();
        assert_eq!(s.value(0), "5");
        assert_eq!(s.value(1), "N/A");
        assert_eq!(s.value(2), "true");
    }

    #[test]
    fn the_recursive_split_payload_becomes_one_nested_array() {
        let arr = build(&["{\"orders\":[{\"id\":1,\"items\":[{\"sku\":\"A\"}]}]}"]);
        let root = arr.as_any().downcast_ref::<StructArray>().unwrap();
        let orders = root
            .column_by_name("orders")
            .unwrap()
            .as_any()
            .downcast_ref::<ListArray>()
            .unwrap();
        assert_eq!(orders.value_length(0), 1);
        let order0 = orders.value(0);
        let o = order0.as_any().downcast_ref::<StructArray>().unwrap();
        assert_eq!(
            o.column_by_name("items")
                .unwrap()
                .as_any()
                .downcast_ref::<ListArray>()
                .unwrap()
                .value_length(0),
            1
        );
    }

    #[test]
    fn field_metadata_carries_the_writer_s_decision() {
        let s = shape_of(&["{\"n\":5}", "{\"n\":\"N/A\"}"]);
        let f = match &s {
            Shape::Struct(fs) => field_for("n", &fs[0].1),
            other => panic!("{other:?}"),
        };
        assert_eq!(f.metadata().get(META_ENCODING).map(String::as_str), Some("token"));
        assert_eq!(f.metadata().get(META_DECLARED).map(String::as_str), Some("number"));
    }
}
