"""Worker-side streaming output converter (SA-1905 C2).

Moves the JSON->CSV conversion from the control plane to the WORKER. Instead of
returning a large list-of-dicts inline — which crosses gRPC (~4 MB) / Kafka
(~1 MB) frame limits and then OOMs the CP's ``dict_to_csv`` (5.5-9.3x blow-up) —
the worker:

  1. writes its output to a JSONL temp file, one row at a time (bounded RAM);
  2. converts it to CSV with the native two-pass streamer
     (``json_csv_streamer.jsonl_to_csv`` — no in-RAM column union);
  3. uploads the CSV to the file service and gets a ``file_id``;
  4. returns a lightweight ``file_ref`` pointer ({__file_ref, file_id, columns,
     column_types, row_count}) — the SAME shape the CP produces, so the CP
     records/resolves it exactly like a ``store_data`` result (no re-conversion).

Gated by ``WORKER_CHUNKED_OUTPUT_ENABLED`` + ``CHUNKING_THRESHOLD_BYTES``.
Disabled / below-threshold / native-wheel-absent / upload failure -> ``convert``
returns ``None`` and the caller keeps the inline output, so behaviour is
unchanged by default and no data is ever lost.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from typing import Any, Optional

from worker_sdk.layer4_frameworks.providers.file_service.http_file_uploader import (
    content_idempotency_key,
)

# Native streaming producer — optional, soft-imported like on the control plane.
try:  # pragma: no cover - import guard
    import json_csv_streamer as _STREAMER
except Exception:  # pragma: no cover
    _STREAMER = None

_logger = logging.getLogger(__name__)

FILE_REF_MARKER = "__file_ref"

# SA-1905 — the Arrow IPC media type, and the reason the sidecar must NOT be
# named ``*.csv``: the file service runs polars canonicalisation + row-id
# injection whenever ``filename.endswith(".csv") or "csv" in mime_type``
# (``canonical_csv_processor._should_inject_row_id``), which would rewrite — and
# destroy — an Arrow IPC file.
ARROW_CONTENT_TYPE = "application/vnd.apache.arrow.file"


class IFileUploader:
    """Structural type: ``async upload(bytes, filename, content_type, idempotency_key) -> file_id``."""

    async def upload(
        self, file_bytes: bytes, filename: str,
        content_type: str = "text/csv", idempotency_key: Optional[str] = None,
    ) -> str: ...  # pragma: no cover


class StreamingOutputConverter:
    """Convert a large worker output to a streamed CSV + a file_id file_ref."""

    def __init__(
        self,
        uploader: IFileUploader,
        enabled: bool = False,
        threshold_bytes: int = 1024 * 1024,
        arrow_sidecar: bool = False,
        stream_scalars: bool = False,
        arrow_codec: str = "",
    ) -> None:
        self._uploader = uploader
        self._enabled = enabled
        self._threshold = threshold_bytes
        # A large STRING root. Its own flag, default off, because the WRITER must
        # not get ahead of the readers: a scalar ref is only safe once
        # ``HttpFileRefResolver`` knows the scalar kinds (it used to call one an
        # object and hand the next node ``{"__value": ...}``). Both ship together
        # here, but a deployment can still turn the writer off on its own.
        self._stream_scalars = stream_scalars
        # SA-1905 — also write a nested Arrow mirror. Its own flag: an unread
        # sidecar is inert, so this can be turned on well before the control
        # plane is ready to read one.
        self._arrow_sidecar = arrow_sidecar
        # Arrow IPC compression: none | lz4 | zstd. Normalised here so the crate
        # never sees whatever was typed into an env var.
        self._arrow_codec = str(arrow_codec or "").strip().lower()
        if self._arrow_codec in ("none", "off"):
            self._arrow_codec = ""

    def _codec_args(self) -> tuple:
        """The codec argument, or NOTHING at all.

        Ten worker images deploy independently, so "new config, old wheel" is
        the normal state during a rollout, not a narrow window. An old wheel's
        ``jsonl_to_*`` take no ``codec`` parameter; passing one raises
        ``TypeError`` inside the sidecar's own ``try``, which degrades to
        CSV-only — silently. Sending nothing when nothing is configured means an
        un-flipped worker makes exactly the call it makes today.

        Pinned by ``test_arrow_codec_plumbing.py``.
        """
        return (self._arrow_codec,) if self._arrow_codec else ()

    def _armed(self) -> bool:
        return bool(self._enabled and _STREAMER is not None and self._uploader is not None)

    def _estimate_bytes(self, output: Any) -> int:
        """Serialized size of *output*, WITHOUT serializing all of it.

        A list samples element 0 and multiplies. A dict samples each top-level
        field the same way, so a `{"title": …, "rows": [30 000 rows]}` is sized
        from one row plus the scalars — the shape that matters here, because it
        is the one that cannot stream today.

        Honest about its own blind spot: a top-level field that is a huge nested
        OBJECT (no arrays) is serialized in full to size it. That payload has no
        cheap sample, and getting its size wrong is how it ends up riding an
        event instead.
        """
        try:
            if isinstance(output, list):
                per = len(json.dumps(output[0], default=str).encode("utf-8"))
                return per * len(output)
            total = 0
            for value in output.values():
                if isinstance(value, list) and value:
                    per = len(json.dumps(value[0], default=str).encode("utf-8"))
                    total += per * len(value)
                else:
                    total += len(json.dumps(value, default=str).encode("utf-8"))
            return total
        except (TypeError, ValueError, AttributeError, IndexError):
            return -1

    def should_stream(self, output: Any) -> bool:
        """True when *output* is large enough to be worth file-backing.

        SA-1977 — no longer requires a list of DICTS, matching the control
        plane's ``_should_stream``. A worker returning a 500 MB ``list[str]`` has
        exactly the problem streaming exists to solve, and the native producer
        handles it (it writes the reserved ``__value`` column). Leaving the two
        gates different would mean the same output streams on one side of the
        wire and rides an event on the other.

        SA-1910 — no longer requires a LIST either. A dict root was the shape
        that could not stream at all, and it is the shape that produces the
        nested artifact tree: a code node returning
        ``{"title": …, "rows": [30 000 rows]}`` had its 2.3 MB published inline
        and refused by the broker —

            message for 'workflow.results' is 2314343 bytes, over the
            1048576-byte broker cap

        — because ``should_stream`` saw a dict and declined, so the executor had
        nothing but the value itself to relay. Splitting it here is what turns
        that message back into a pointer.
        """
        if not self._armed():
            return False
        # Phase 3 (run edda4f82) — a STRING root. The field that broke the broker
        # cap was ``resolved_body``, a str, and the failure message pointed at
        # streaming, which could not touch it. Measured on the encoded bytes,
        # which is what the wire carries.
        if isinstance(output, str):
            if not self._stream_scalars or not output:
                return False
            return len(output.encode("utf-8")) >= self._threshold
        if not isinstance(output, (list, dict)) or not output:
            return False
        if isinstance(output, dict) and not hasattr(_STREAMER, "jsonl_split_recursive"):
            # An older wheel has no splitter. Declining is the pre-SA-1910
            # behaviour, which is survivable; guessing a flat CSV for a dict
            # would silently drop the tree.
            return False
        size = self._estimate_bytes(output)
        return size >= 0 and size >= self._threshold

    async def convert(
        self, task_id: str, field: str, output: Any,
        uploaded: Optional[list] = None,
    ) -> Optional[dict]:
        """Stream *output* to CSV, upload it, and return a file_ref — or ``None``
        to keep it inline. Never raises: any failure returns ``None`` so the
        caller falls back to the inline path (no data loss).

        ``uploaded``, when given, collects every ``file_id`` created here —
        INCLUDING on the paths that then return ``None``. That is the point:
        the ones worth reporting are exactly the ones nothing ends up pointing
        at. See ``_maybe_stream_outputs``.
        """
        if not self.should_stream(output):
            return None
        if isinstance(output, dict):
            return await self._convert_tree(task_id, field, output, uploaded)
        # A scalar root rides the SAME machinery as a list — one JSONL line, and
        # the native producer already writes the reserved ``__value`` column for
        # it (that is how ``list[str]`` works since SA-1977). Only the
        # DECLARATION differs, at the bottom of this method: ``array`` would read
        # back as a one-element list, ``string`` reads back as the value.
        scalar_root = isinstance(output, str)
        if scalar_root:
            output = [output]
        tmpdir = None
        try:
            # mkdtemp is INSIDE the try: a failing/full TMPDIR must return None
            # (inline fallback), not raise — the callers on the async execute
            # path do not all wrap this, so a raise would kill the background
            # task with no callback and hang the node.
            tmpdir = tempfile.mkdtemp(prefix="worker-chunk-")
            jsonl_path = os.path.join(tmpdir, "out.jsonl")
            csv_path = os.path.join(tmpdir, "out.csv")
            with open(jsonl_path, "w", encoding="utf-8") as fh:
                for row in output:
                    fh.write(json.dumps(row, default=str))
                    fh.write("\n")
            arrow_path = os.path.join(tmpdir, "out.arrow")
            arrow_written = False
            if self._arrow_sidecar and hasattr(
                _STREAMER, "jsonl_to_csv_and_nested_arrow"
            ):
                try:
                    # One scan, two sketches, two writers. The crate asserts the
                    # CSV bytes are byte-identical to the CSV-only path, which is
                    # what makes writing a second artifact safe at all.
                    columns, column_types, row_count, item_kind = (
                        _STREAMER.jsonl_to_csv_and_nested_arrow(
                            jsonl_path, csv_path, arrow_path, *self._codec_args(),
                        )
                    )
                    arrow_written = True
                except Exception as exc:
                    # Its OWN try, deliberately. The outer handler returns None,
                    # which means KEEP THE OUTPUT INLINE — and an oversized
                    # inline output is SA-1951: it rides a NodeOutputProduced
                    # event into an Event Mesh publish, 413s at ~1 MB, and the
                    # run hangs forever. A sidecar hiccup must never buy that.
                    _logger.warning(
                        "SA-1905 arrow sidecar failed for task=%s field=%s (%s); "
                        "writing the CSV alone", task_id, field, exc,
                    )
            if not arrow_written:
                columns, column_types, row_count, item_kind = _STREAMER.jsonl_to_csv(
                    jsonl_path, csv_path,
                )
            with open(csv_path, "rb") as fh:
                csv_bytes = fh.read()
            if not csv_bytes:
                return None
            file_id = await self._uploader.upload(
                csv_bytes, f"{task_id}-{field}.csv", "text/csv",
                idempotency_key=content_idempotency_key(csv_bytes),
            )
            if not file_id:
                return None
            if uploaded is not None:
                uploaded.append(file_id)

            arrow_file_id, arrow_size = "", 0
            if arrow_written:
                try:
                    with open(arrow_path, "rb") as fh:
                        arrow_bytes = fh.read()
                    if arrow_bytes:
                        arrow_file_id = await self._uploader.upload(
                            arrow_bytes,
                            f"{task_id}-{field}.arrow",
                            ARROW_CONTENT_TYPE,
                            idempotency_key=content_idempotency_key(arrow_bytes),
                        )
                        arrow_size = len(arrow_bytes) if arrow_file_id else 0
                        if arrow_file_id and uploaded is not None:
                            uploaded.append(arrow_file_id)
                except Exception as exc:
                    # Same reasoning as above: the CSV ref is already good.
                    _logger.warning(
                        "SA-1905 arrow sidecar upload failed for task=%s field=%s "
                        "(%s); the CSV ref is unaffected", task_id, field, exc,
                    )
                    arrow_file_id, arrow_size = "", 0

            return {
                FILE_REF_MARKER: True,
                "file_id": file_id,
                "content_type": "text/csv",
                "row_count": row_count,
                "columns": columns,
                "column_types": column_types,
                # SA-1977 — a worker-streamed ref declares its root kind exactly
                # like a control-plane-stored one, or the CP would have to guess
                # the kind of the very values that are too big to guess about.
                # ``should_stream`` gates on a non-empty list, so the root is an
                # array; the element kind comes from the streamer's PASS 1. A
                # scalar root declares itself instead — and declares NO item
                # kind, because it has no elements to have a kind.
                "value_kind": "string" if scalar_root else "array",
                "item_kind": "" if scalar_root else item_kind,
                "artifacts": [],
                # SA-1905 — a TOP-LEVEL key, never an entry in ``artifacts``:
                # five control-plane consumers walk that list without filtering
                # on type and would each break differently. Merged in only when
                # a sidecar exists, so a ref without one keeps exactly the key
                # set it had before this feature.
                **(
                    {"arrow_file_id": arrow_file_id, "arrow_size_bytes": arrow_size}
                    if arrow_file_id
                    else {}
                ),
            }
        except Exception as exc:
            # Never raise: the caller keeps the inline output (no data loss). But
            # never do it SILENTLY either — falling back to inline is exactly how
            # SA-1951 happens (a multi-megabyte value rides a NodeOutputProduced
            # event straight into an Event Mesh publish, which 413s at ~1 MB, and
            # the run hangs forever with no error). The control plane's twin
            # (``_store_list_streaming``) already logs; this one swallowed. A
            # wheel/ABI mismatch would have been invisible.
            _logger.warning(
                "Worker-side streaming conversion failed for task=%s field=%s (%s); "
                "keeping the output INLINE — an oversized inline output cannot ride "
                "an event", task_id, field, exc,
            )
            return None
        finally:
            if tmpdir is not None:
                shutil.rmtree(tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # SA-1910 — the dict root: build the artifact tree here, relay a pointer
    # ------------------------------------------------------------------

    async def _convert_tree(
        self, task_id: str, field: str, output: dict,
        uploaded: Optional[list] = None,
    ) -> Optional[dict]:
        """Split a dict output into the nested-array CSV tree and return its ref.

        This is the path that removes the broker failure. A dict root could not
        stream at all, so a code node returning ``{"title": …, "rows": […]}``
        published its whole value on ``workflow.results`` and was refused at
        1 048 576 bytes. Here the tree is built worker-side and only the pointer
        travels.

        The tree itself comes from the crate (``jsonl_split_recursive``), the
        same producer the control plane uses, so the two sides cannot drift on
        what the tree IS. What stays here is what needs a credential: the
        uploads, and spelling the ref.

        Never raises — same contract as ``convert``: on any failure the caller
        keeps the output inline.
        """
        tmpdir = None
        try:
            tmpdir = tempfile.mkdtemp(prefix="worker-tree-")
            jsonl_path = os.path.join(tmpdir, "out.jsonl")
            # One line: a dict root is a one-row table, exactly as the control
            # plane's ``_store_rows_recursive(key, [data], as_list=False)``.
            with open(jsonl_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(output, default=str))
                fh.write("\n")

            manifest = _STREAMER.jsonl_split_recursive(jsonl_path, tmpdir)

            base = f"{task_id}-{field}"
            root = await self._upload_tree(manifest, base, uploaded)
            if root is None:
                return None

            ref = {
                FILE_REF_MARKER: True,
                "file_id": root["file_id"],
                "content_type": "text/csv",
                "row_count": manifest["row_count"],
                "columns": list(manifest["columns"]),
                "column_types": dict(manifest["column_types"]),
                "size_bytes": manifest["size_bytes"],
                # A dict root is an OBJECT — the one thing the CP cannot infer
                # from the CSV, because a one-row table and a single object look
                # identical once written (SA-1977).
                "value_kind": "object",
                "item_kind": "",
                "artifacts": root["artifacts"],
            }

            arrow_id, arrow_size = await self._upload_nested_sidecar(
                jsonl_path, tmpdir, base, uploaded,
            )
            if arrow_id:
                ref["arrow_file_id"] = arrow_id
                ref["arrow_size_bytes"] = arrow_size
            return ref
        except Exception as exc:
            _logger.warning(
                "SA-1910 worker-side tree split failed for task=%s field=%s (%s); "
                "keeping the output INLINE — an oversized inline output cannot "
                "ride an event", task_id, field, exc,
            )
            return None
        finally:
            if tmpdir is not None:
                shutil.rmtree(tmpdir, ignore_errors=True)

    async def _upload_tree(
        self, node: dict, base: str, uploaded: Optional[list] = None,
    ) -> Optional[dict]:
        """Upload one manifest node and its descendants; return the artifact body.

        Depth-first, so a child's ``file_id`` exists before its parent's
        artifact entry is written. Returns ``None`` if any upload fails: a
        half-uploaded tree is a ref whose join silently loses rows, which is
        worse than staying inline.
        """
        with open(node["csv_path"], "rb") as fh:
            csv_bytes = fh.read()
        if not csv_bytes:
            return None
        file_id = await self._uploader.upload(
            csv_bytes, f"{base}.csv", "text/csv",
            # From the manifest, not recomputed: the crate hashed the exact
            # bytes it wrote, and a key computed over anything else would make
            # the file service dedupe against content it does not hold.
            idempotency_key=node["content_key"],
        )
        if not file_id:
            return None
        if uploaded is not None:
            uploaded.append(file_id)

        artifacts = []
        for child in node["children"]:
            sub = await self._upload_tree(
                child, f"{base}-{child['name']}", uploaded,
            )
            if sub is None:
                return None
            artifacts.append({
                # ``artifact_id`` defaults to the name, matching add_artifact.
                "artifact_id": child["name"],
                "file_id": sub["file_id"],
                "name": child["name"],
                "content_type": "text/csv",
                "size_bytes": child["size_bytes"],
                "artifact_type": "nested_array",
                "parent_row_key_column": "__parent_row_id",
                "column_types": dict(child["column_types"]),
                "columns": list(child["columns"]),
                "row_count": child["row_count"],
                # A nested_array child is always a table of objects.
                "value_kind": "array",
                "item_kind": "object",
                "artifacts": sub["artifacts"],
            })
        return {"file_id": file_id, "artifacts": artifacts}

    async def _upload_nested_sidecar(
        self, jsonl_path: str, tmpdir: str, base: str,
        uploaded: Optional[list] = None,
    ) -> tuple[str, int]:
        """SA-1905 — ONE Arrow mirror of the whole value, at the root.

        Its own try, and its own flag. An unread sidecar is inert, but a sidecar
        failure must never cost the CSV tree that was just uploaded — that would
        trade a working pointer for an inline value the broker refuses.
        """
        if not self._arrow_sidecar or not hasattr(_STREAMER, "jsonl_to_nested_arrow"):
            return "", 0
        try:
            arrow_path = os.path.join(tmpdir, "out.arrow")
            _STREAMER.jsonl_to_nested_arrow(jsonl_path, arrow_path, *self._codec_args())
            with open(arrow_path, "rb") as fh:
                arrow_bytes = fh.read()
            if not arrow_bytes:
                return "", 0
            file_id = await self._uploader.upload(
                arrow_bytes, f"{base}.arrow", ARROW_CONTENT_TYPE,
                idempotency_key=content_idempotency_key(arrow_bytes),
            )
            if file_id and uploaded is not None:
                uploaded.append(file_id)
            return (file_id, len(arrow_bytes)) if file_id else ("", 0)
        except Exception as exc:
            _logger.warning(
                "SA-1905 nested sidecar failed for %s (%s); the CSV tree is "
                "unaffected", base, exc,
            )
            return "", 0
