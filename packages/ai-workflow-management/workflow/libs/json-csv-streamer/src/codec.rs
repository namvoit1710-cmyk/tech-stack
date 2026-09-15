//! One place that turns a codec NAME into `IpcWriteOptions`.
//!
//! There are four Arrow writers in this crate and they must not disagree about
//! what `"zstd"` means — the same argument that put the CSV and the sidecar
//! behind a single `sketch`.
//!
//! Two rules, both of them consequences of where the name comes from (an
//! environment variable, read per call):
//!
//! * **`None` is the library default, exactly.** `FileWriter::try_new` is
//!   defined as `try_new_with_options(.., IpcWriteOptions::default())`, so
//!   returning the default here makes the codec-aware path byte-identical to
//!   the one it replaces. That is what lets the wheel ship to all 11 images
//!   inert, ahead of any writer being flipped — the rollout order in
//!   `wcp-arrow-size-reduction-plan.md` §1.6 depends on it, and
//!   `tests_codec::default_codec_is_byte_identical_to_the_old_constructor`
//!   holds it.
//! * **An unknown name degrades; it never raises.** A typo in an env var must
//!   cost compression, not a run's output. The alternative is a node that
//!   cannot store its result because somebody wrote `ZSTD9`.
//!
//! Reading the value per call rather than caching it at import is deliberate:
//! `TASK_METADATA_MODE` is import-time and consequently needs a restart to move,
//! which is a papercut this crate does not need to reproduce.

use arrow::ipc::writer::IpcWriteOptions;
use arrow::ipc::CompressionType;

/// `IpcWriteOptions` for a codec name. Never fails.
pub(crate) fn writer_options(codec: Option<&str>) -> IpcWriteOptions {
    let base = IpcWriteOptions::default();
    let name = match codec {
        None => return base,
        Some(raw) => raw.trim().to_ascii_lowercase(),
    };
    let wanted = match name.as_str() {
        "" | "none" | "off" => return base,
        "lz4" | "lz4_frame" => CompressionType::LZ4_FRAME,
        "zstd" => CompressionType::ZSTD,
        other => {
            // Not a panic and not an error: see the module docstring. Stderr
            // because this crate has no logger and the caller's Python logging
            // is not reachable from inside `allow_threads`.
            eprintln!(
                "json-csv-streamer: unknown arrow codec {other:?}; writing uncompressed"
            );
            return base;
        }
    };
    // The Err arm is reachable only in a build without the `ipc_compression`
    // feature. Degrading there is the same decision as an unknown name: a wheel
    // built without the feature still writes readable files.
    match IpcWriteOptions::default().try_with_compression(Some(wanted)) {
        Ok(opts) => opts,
        Err(e) => {
            eprintln!(
                "json-csv-streamer: arrow codec {name:?} unavailable in this build ({e}); \
                 writing uncompressed"
            );
            base
        }
    }
}
