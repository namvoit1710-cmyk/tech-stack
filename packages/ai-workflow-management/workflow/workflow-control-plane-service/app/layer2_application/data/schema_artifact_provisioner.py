"""Pre-provision empty files for schema-declared artifacts at dispatch time.

When a PENDING node starts and its auto-form schema declares a ``TableWrapper``
artifact (at any depth) that has no corresponding file_id in the resolved
input_binding, this service creates an empty CSV with the declared columns and
splices it into the working ``__file_ref`` tree. Subsequent ``PATCH /data-v2``
calls can then target the artifact_id directly instead of 404-ing.

Merge semantics (Q3 = merge-and-fill-gaps): if the binding already produced a
file_ref tree, we only provision artifacts whose dotted-path is **missing** from
that tree. Existing file_ids are preserved; nested gaps are filled independently
of whether ancestors were provided.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.layer1_domain.entities.data_reference import (
    refresh_schema_hash,
    ROOT_ARTIFACT_ID,
    add_artifact,
    build_file_ref,
    find_artifact,
    is_file_ref,
)
from app.layer1_domain.value_objects.csv_document import CsvDocument, ROW_ID_COLUMN
from app.layer1_domain.value_objects.auto_form_schema import AutoFormSchema, SchemaArtifact
from app.layer2_application.interfaces.file_service_client_interface import (
    IFileServiceClient,
)

logger = logging.getLogger(__name__)

NESTED_PARENT_ROW_KEY = "__parent_row_id"


async def provision_schema_artifacts(
    *,
    input_schema: Any,
    existing_ref: Any,
    file_client: IFileServiceClient,
    node_id: str = "",
    force_root: bool = False,
) -> dict[str, Any]:
    """Return a ``__file_ref`` tree covering every schema-declared artifact.

    - If ``existing_ref`` is a file_ref, merges into a deep-copied version
      (only missing artifacts get created).
    - Otherwise starts from a fresh empty root ref and registers every
      declared artifact at its dotted path.
    - Creates an empty CSV (header-only) per missing artifact via
      ``file_client.upload_file``.
    - Nested artifacts get ``parent_row_key_column="__parent_row_id"``.

    Returns the (possibly mutated) file_ref dict. If no artifacts are declared
    in the schema, returns ``existing_ref`` unchanged (or ``None`` if it wasn't
    one to begin with) — UNLESS ``force_root`` is set, in which case a
    header-only (``__row_id``-only) root CSV is still minted so the node has a
    non-empty ``__root__.file_id``. INPUT passes ``force_root=True`` because it
    has no synthetic scalar to inject (unlike HUMAN_ACTION's ``_action`` /
    FILE_UPLOAD's ``file``), yet must still uphold the uniformity invariant.
    """
    artifacts = AutoFormSchema(input_schema).artifacts()
    scalar_cols = AutoFormSchema(input_schema).scalar_columns()
    scalar_defaults = AutoFormSchema(input_schema).scalar_defaults()
    if not artifacts and not scalar_cols and not force_root:
        return existing_ref

    # Build or copy the root ref. Root always present so nested inserts work.
    if is_file_ref(existing_ref):
        import copy
        root = copy.deepcopy(existing_ref)
    else:
        root = build_file_ref(file_id="", artifact_id=ROOT_ARTIFACT_ID)

    # Root CSV: one file per node holding every scalar as a column —
    # top-level scalars under their flat key, scalars nested in object
    # containers (tabs/sections) under their dotted path
    # (``tabs.tab_1.created_at``). Always provisioned (even for array-only
    # schemas) so every input-emitting node has a non-empty
    # ``__root__.file_id`` — uniformity for the read side. Seeded with a
    # single data row when any scalar declares a ``default``; otherwise
    # header-only and a PATCH /data later upserts the row.
    #
    # ``artifact_type``/``content_type`` are stamped UNCONDITIONALLY so the root
    # is always typed ``object_field`` — even when the CSV upload below fails and
    # leaves ``file_id`` empty. PATCH /data routes a root to the local object
    # upsert path by type; a typeless root (the old behaviour when the upload was
    # swallowed to "") fell through to the data-factory and 400'd with "file_id
    # is required". With the type stamped, an empty-file_id root still upserts
    # locally and self-mints a file on the first edit.
    if not root.get("file_id"):
        root["artifact_type"] = "object_field"
        root["content_type"] = "text/csv"
        seed_rows: list[dict[str, Any]] = []
        if scalar_defaults:
            seed_rows = [{k: scalar_defaults[k] for k in scalar_defaults}]
        root_file_id = await _create_seeded_csv(
            file_client, scalar_cols, seed_rows, name="root", node_id=node_id,
        )
        if root_file_id:
            root["file_id"] = root_file_id
            cols = [ROW_ID_COLUMN] + [c for c in scalar_cols if c != ROW_ID_COLUMN]
            root["columns"] = cols
            refresh_schema_hash(root)

    # Provision artifacts in depth order so parents exist before children.
    for art in sorted(artifacts, key=lambda a: a.depth):
        if find_artifact(root, art.artifact_id) is not None:
            continue
        parent_id = _parent_artifact_id(art.artifact_id)
        parent = find_artifact(root, parent_id) if parent_id else root
        if parent is None:
            # Parent artifact not declared in schema — attach to root as flat.
            parent = root

        # Nested arrays (depth > 0) need __parent_row_id wiring that can't be
        # synthesised at provision time, so defaults are only seeded for
        # top-level (root) artifacts. Nested defaults are deferred.
        seed_rows = list(art.default_rows) if art.depth == 0 else []
        file_id = await _create_seeded_csv(
            file_client, art.columns, seed_rows,
            name=art.name, node_id=node_id,
        )
        add_artifact(
            parent,
            file_id=file_id,
            name=art.name,
            artifact_id=art.artifact_id,
            artifact_type="nested_array_field" if art.depth > 0 else "array_field",
            parent_row_key_column=(
                NESTED_PARENT_ROW_KEY if art.depth > 0 else None
            ),
        )
        # Populate columns on the artifact dict so downstream reads see the schema.
        created = find_artifact(root, art.artifact_id)
        if created is not None:
            cols = list(art.columns)
            if art.depth > 0 and NESTED_PARENT_ROW_KEY not in cols:
                cols.insert(0, NESTED_PARENT_ROW_KEY)
            created["columns"] = cols
            refresh_schema_hash(created)

    return root


def _parent_artifact_id(artifact_id: str) -> str:
    if "." not in artifact_id:
        return ROOT_ARTIFACT_ID
    return artifact_id.rsplit(".", 1)[0]


async def _create_seeded_csv(
    file_client: IFileServiceClient,
    columns: list[str],
    seed_rows: list[dict[str, Any]],
    *,
    name: str,
    node_id: str,
) -> str:
    """Upload a CSV (header + optional seed rows) and return its file_id.

    When ``seed_rows`` is empty, behaves identically to the original
    header-only ``_create_empty_csv`` path. Each seed row gets a fresh
    ``__row_id`` so downstream PATCH ops can address it by the standard key.
    Cells outside ``columns`` are dropped via ``DictWriter(extrasaction="ignore")``.
    """
    cols = list(columns)
    # Mirror csv_ops.inject_row_ids: prepend the row-id column so edits that
    # target this artifact later can address rows by the standard key.
    if ROW_ID_COLUMN not in cols:
        cols.insert(0, ROW_ID_COLUMN)
    filename = f"{node_id or 'node'}_{name or 'artifact'}.csv"
    if seed_rows:
        # __row_id must be integers (file-service contract): assign 1..N.
        rows_with_ids = [
            {**row, ROW_ID_COLUMN: str(i)}
            for i, row in enumerate(seed_rows, start=1)
        ]
        body = CsvDocument.from_rows(cols, rows_with_ids)
    else:
        body = (",".join(cols) + "\n").encode("utf-8")
    logger.info(
        "artifact provisioning upload START filename=%s node_id=%s cols=%s seed_rows=%d bytes=%d",
        filename, node_id, cols, len(seed_rows or []), len(body or b""),
    )
    try:
        up = await file_client.upload_file(body, filename, "text/csv")
    except Exception:  # pragma: no cover — network safety net
        logger.exception("artifact provisioning failed for %s", filename)
        return ""
    file_id = up.get("file_id", "") or ""
    if not file_id:
        # Upload "succeeded" at the call level but returned no file_id (the
        # HttpFileServiceClient swallows 4xx/5xx into {} — see its logs for the
        # status/body). Without a file_id the provisioned artifact has no CSV
        # backing it; the node will render with file_id="" and persist no data.
        logger.error(
            "artifact provisioning got NO file_id from file service "
            "filename=%s node_id=%s resp=%r",
            filename, node_id, up,
        )
    else:
        logger.info(
            "artifact provisioning upload OK filename=%s node_id=%s file_id=%s",
            filename, node_id, file_id,
        )
    return file_id


def node_schema_declares_artifacts(input_schema: Any) -> bool:
    """Fast check used by handlers to decide whether provisioning is relevant.

    True when the schema declares **any** file-backed payload — either a
    table (nested or otherwise) or at least one top-level scalar (which
    becomes the root CSV).
    """
    return bool(AutoFormSchema(input_schema).artifacts()) or bool(
        AutoFormSchema(input_schema).scalar_columns()
    )


def inject_provisioned_ref(
    resolved_input: Optional[dict[str, Any]],
    file_ref: dict[str, Any],
    *,
    key: str = "__file_ref_tree",
) -> dict[str, Any]:
    """Attach a provisioned file_ref tree to a handler's resolved_input dict.

    Returns a new dict so handlers don't mutate their inputs. Idempotent —
    overwrites any prior value at the same key.
    """
    out = dict(resolved_input or {})
    out[key] = file_ref
    return out
