//! Phase 1 — Arrow IPC compression. Tests written BEFORE the implementation.
//!
//! Why these and not "does zstd work" (arrow-rs already tests that): every test
//! here pins a decision this codebase has to keep, and each one is a way the
//! rollout could go wrong silently.
//!
//! 1. `none` must be **byte-identical** to the constructor used today. That is
//!    what lets the wheel ship inert across 11 images before any writer is
//!    flipped — see `solace/specs/wcp-arrow-size-reduction-plan.md` §1.6. If it
//!    is not byte-identical, "inert" is a claim rather than a fact.
//! 2. An unknown codec must **degrade, not raise**. The codec arrives from an
//!    env var; a typo must cost compression, never a run's output.
//! 3. Round-trip must preserve VALUES, not bytes — compression changes the file
//!    and must not change the reading.

use super::*;

use arrow::ipc::reader::FileReader;
use arrow::ipc::writer::{FileWriter, IpcWriteOptions};
use std::fs::File;
use std::io::BufWriter;

use crate::codec::writer_options;

fn tmpdir(tag: &str) -> std::path::PathBuf {
    let d = std::env::temp_dir().join(format!("jcs-codec-{tag}"));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// Rows that actually compress — repeated low-cardinality strings, which is
/// exactly the shape measured live (plant codes, UoM codes, status strings).
fn compressible_jsonl(rows: usize) -> String {
    let mut s = String::new();
    for i in 0..rows {
        s.push_str(&format!(
            "{{\"sku\":\"MATERIAL-{}\",\"plant\":\"PLNT0001\",\"uom\":\"EA\",\
              \"status\":\"ACTIVE\",\"desc\":\"the same sentence on every row\",\"qty\":{}}}\n",
            i % 50,
            i,
        ));
    }
    s
}

fn read_back(path: &str) -> Vec<arrow::array::RecordBatch> {
    let f = File::open(path).unwrap();
    FileReader::try_new(f, None)
        .unwrap()
        .map(|b| b.unwrap())
        .collect()
}

fn write_flat(dir: &std::path::Path, name: &str, lines: &str, codec: Option<&str>) -> String {
    let inp = dir.join("in.jsonl");
    std::fs::write(&inp, lines).unwrap();
    let outp = dir.join(name);
    let schema = sketch(inp.to_str().unwrap()).unwrap();
    let hints = schema.numeric_hints();
    let mixed = schema.mixed_leaf_columns();
    let (cols, types, _item, vm, jc) = schema.finish();
    crate::arrow_sidecar::write_arrow_impl(
        inp.to_str().unwrap(),
        outp.to_str().unwrap(),
        &cols,
        &types,
        &hints,
        &mixed,
        jc,
        vm,
        codec,
    )
    .unwrap();
    outp.to_str().unwrap().to_owned()
}

// ---------------------------------------------------------------------------
// 1. `none` is inert — the property the whole rollout order rests on
// ---------------------------------------------------------------------------

/// `writer_options(None)` must BE the library default, because
/// `FileWriter::try_new` is defined as `try_new_with_options(.., default())`.
/// Anything else and today's files silently change shape on the wheel bump.
#[test]
fn none_is_exactly_the_library_default() {
    assert_eq!(
        format!("{:?}", writer_options(None)),
        format!("{:?}", IpcWriteOptions::default()),
    );
    assert_eq!(
        format!("{:?}", writer_options(Some("none"))),
        format!("{:?}", IpcWriteOptions::default()),
    );
    assert_eq!(
        format!("{:?}", writer_options(Some(""))),
        format!("{:?}", IpcWriteOptions::default()),
    );
}

/// The end-to-end version of the same claim: a file written with the new
/// codec-aware path and `None` is byte-for-byte a file written with the
/// constructor this crate uses today.
#[test]
fn default_codec_is_byte_identical_to_the_old_constructor() {
    let d = tmpdir("identical");
    let lines = compressible_jsonl(2_000);
    let new_path = write_flat(&d, "new.arrow", &lines, None);

    // The old constructor, reproduced here so the assertion survives the
    // call site being changed.
    let old_path = d.join("old.arrow");
    let batches = read_back(&new_path);
    let schema = batches[0].schema();
    {
        let f = File::create(&old_path).unwrap();
        let mut w = FileWriter::try_new(BufWriter::new(f), &schema).unwrap();
        for b in &batches {
            w.write(b).unwrap();
        }
        w.finish().unwrap();
    }

    assert_eq!(
        std::fs::read(&new_path).unwrap(),
        std::fs::read(&old_path).unwrap(),
        "codec=None changed the bytes; the wheel would NOT ship inert",
    );
    let _ = std::fs::remove_dir_all(&d);
}

// ---------------------------------------------------------------------------
// 2. An unknown codec degrades — it never fails a run
// ---------------------------------------------------------------------------

#[test]
fn unknown_codec_degrades_to_uncompressed_instead_of_raising() {
    assert_eq!(
        format!("{:?}", writer_options(Some("gzip"))),
        format!("{:?}", IpcWriteOptions::default()),
        "an env typo must cost compression, not the output",
    );

    let d = tmpdir("unknown");
    let path = write_flat(&d, "typo.arrow", &compressible_jsonl(100), Some("GZIP-9"));
    let batches = read_back(&path);
    assert_eq!(batches.iter().map(|b| b.num_rows()).sum::<usize>(), 100);
    let _ = std::fs::remove_dir_all(&d);
}

/// Case and surrounding whitespace come from an env var, so they are the
/// caller's mess to survive, not to reject.
#[test]
fn codec_name_is_case_and_whitespace_insensitive() {
    for name in ["ZSTD", " zstd ", "Zstd"] {
        assert_eq!(
            format!("{:?}", writer_options(Some(name))),
            format!("{:?}", writer_options(Some("zstd"))),
            "{name:?} did not resolve to zstd",
        );
    }
}

// ---------------------------------------------------------------------------
// 3. Round-trip: the file changes, the reading does not
// ---------------------------------------------------------------------------

#[test]
fn every_codec_round_trips_the_same_values() {
    let lines = compressible_jsonl(3_000);
    let mut readings = Vec::new();
    for codec in [None, Some("lz4"), Some("zstd")] {
        let tag = codec.unwrap_or("none");
        let d = tmpdir(&format!("rt-{tag}"));
        let path = write_flat(&d, "out.arrow", &lines, codec);
        let batches = read_back(&path);
        // A canonical rendering of every value, so the comparison is about the
        // DATA and not about batch boundaries or buffer layout.
        let rendered = format!("{:?}", batches);
        let size = std::fs::metadata(&path).unwrap().len();
        readings.push((tag, rendered, size));
        let _ = std::fs::remove_dir_all(&d);
    }
    assert_eq!(readings[0].1, readings[1].1, "lz4 changed the values");
    assert_eq!(readings[0].1, readings[2].1, "zstd changed the values");
}

/// The point of the phase, asserted rather than assumed. Measured live at
/// 3.4x (lz4) / 4.3x (zstd); 1.5x is a floor that catches "compression was
/// silently not applied" without being flaky on a small corpus.
#[test]
fn compression_actually_shrinks_the_file() {
    let lines = compressible_jsonl(3_000);
    let mut sizes = Vec::new();
    for codec in [None, Some("lz4"), Some("zstd")] {
        let tag = codec.unwrap_or("none");
        let d = tmpdir(&format!("sz-{tag}"));
        let path = write_flat(&d, "out.arrow", &lines, codec);
        sizes.push((tag, std::fs::metadata(&path).unwrap().len()));
        let _ = std::fs::remove_dir_all(&d);
    }
    let none = sizes[0].1 as f64;
    for (tag, sz) in &sizes[1..] {
        assert!(
            none / (*sz as f64) > 1.5,
            "{tag}: {sz} vs uncompressed {none} — compression did not apply",
        );
    }
}

// ---------------------------------------------------------------------------
// 4. The nested tree — the writer that produced the 13.68 MB file
// ---------------------------------------------------------------------------

#[test]
fn the_nested_tree_round_trips_under_every_codec() {
    // Two levels of nesting plus a sparse child: the shapes the live payload has.
    let lines = "{\"id\":1,\"deep\":[{\"k\":\"A\",\"sub\":[{\"x\":1},{\"x\":2}]}]}\n\
                 {\"id\":2,\"deep\":[]}\n\
                 {\"id\":3,\"deep\":[{\"k\":\"B\",\"sub\":[]}]}\n"
        .repeat(300);

    let mut readings = Vec::new();
    for codec in [None, Some("lz4"), Some("zstd")] {
        let tag = codec.unwrap_or("none");
        let d = tmpdir(&format!("nest-{tag}"));
        let inp = d.join("in.jsonl");
        std::fs::write(&inp, &lines).unwrap();
        let outp = d.join("out.arrow");

        let (shape, struct_root) = crate::nested_io::sketch_tree(inp.to_str().unwrap()).unwrap();
        let n = crate::nested_io::write_nested_impl(
            inp.to_str().unwrap(),
            outp.to_str().unwrap(),
            &shape,
            struct_root,
            codec,
        )
        .unwrap();
        assert_eq!(n, 900, "{tag}: row count moved");

        let batches = read_back(outp.to_str().unwrap());
        readings.push((
            tag,
            format!("{:?}", batches),
            std::fs::metadata(&outp).unwrap().len(),
        ));
        let _ = std::fs::remove_dir_all(&d);
    }
    assert_eq!(readings[0].1, readings[1].1, "lz4 changed the nested tree");
    assert_eq!(readings[0].1, readings[2].1, "zstd changed the nested tree");
    assert!(
        readings[0].2 > readings[2].2,
        "zstd did not shrink the nested tree ({} -> {})",
        readings[0].2,
        readings[2].2,
    );
}
