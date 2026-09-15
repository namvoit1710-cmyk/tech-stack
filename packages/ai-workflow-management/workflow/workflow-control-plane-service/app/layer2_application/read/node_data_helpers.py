"""Shared helpers for building variable paths, inferring types, and extracting file info from node context data."""

from __future__ import annotations

from typing import Any


def infer_type(value: Any) -> str:
    """Infer a type string from a value (often a CSV string)."""
    if value is None:
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        v = value.strip()
        if v.lower() in ("true", "false"):
            return "boolean"
        try:
            float(v)
            return "number"
        except ValueError:
            pass
        if (v.startswith("{") and v.endswith("}")) or (v.startswith("[") and v.endswith("]")):
            return "object" if v.startswith("{") else "array"
    return "string"


def paths_from_context(value: Any) -> list[dict[str, Any]]:
    """Build path dicts from a runtime context value.

    Returns list of {"path", "type", "label", "sample"} dicts.
    """
    if not isinstance(value, dict):
        return []

    from app.layer1_domain.entities.data_reference import FILE_REF_MARKER
    if value.get(FILE_REF_MARKER) is True:
        paths: list[dict[str, Any]] = []
        extracted = value.get("extracted", {})
        for path_key in sorted(extracted.keys()):
            entry = extracted[path_key]
            sample = entry.get("value") if isinstance(entry, dict) else None
            paths.append({
                "path": path_key,
                "type": type(sample).__name__ if sample is not None else "string",
                "label": path_key.rsplit(".", 1)[-1],
                "sample": sample,
            })
        columns = value.get("columns", [])
        existing = {p["path"] for p in paths}
        for col in columns:
            if col not in existing:
                paths.append({"path": col, "type": "string", "label": col.rsplit(".", 1)[-1], "sample": None})
        return paths

    paths = []
    for top_key in sorted(value.keys()):
        child = value[top_key]
        if isinstance(child, dict):
            for sub_key in sorted(child.keys()):
                paths.append({
                    "path": f"{top_key}.{sub_key}",
                    "type": type(child[sub_key]).__name__,
                    "label": sub_key,
                    "sample": child[sub_key],
                })
        else:
            paths.append({
                "path": top_key,
                "type": type(child).__name__,
                "label": top_key,
                "sample": child,
            })
    return paths


def meta_paths_for_file_ref(value: dict[str, Any], file_id: str | None) -> list[dict[str, Any]]:
    """Build meta-path dicts for a file-backed reference."""
    from app.layer1_domain.entities.data_reference import get_artifacts
    paths: list[dict[str, Any]] = [
        {"path": "__file_id", "type": "string", "label": "File ID", "sample": file_id or value.get("file_id", "")},
        {"path": "__row_count", "type": "number", "label": "Row Count", "sample": value.get("row_count", 0)},
        {"path": "__columns", "type": "array", "label": "Columns", "sample": None},
        {"path": "__content_type", "type": "string", "label": "Content Type", "sample": value.get("content_type", "")},
        {"path": "__artifacts", "type": "array", "label": "Artifacts", "sample": None},
    ]
    for i, art in enumerate(get_artifacts(value)):
        name = art.get("name", "")
        paths.append({
            "path": f"__artifacts[{i}].file_id",
            "type": "string",
            "label": f"Artifact: {name}",
            "sample": art.get("file_id", ""),
        })
        if name:
            paths.append({
                "path": f"__artifact.{name}",
                "type": "string",
                "label": f"Artifact by name: {name}",
                "sample": art.get("file_id", ""),
            })
    return paths


def extract_file_id(value: Any) -> str | None:
    """Extract file_id from a context value if it's a file reference."""
    if not isinstance(value, dict):
        return None
    from app.layer1_domain.entities.data_reference import FILE_REF_MARKER
    if value.get(FILE_REF_MARKER) is True:
        fid = value.get("file_id")
        return str(fid) if fid else None
    output = value.get("output")
    if isinstance(output, dict):
        uri = output.get("uri")
        if uri:
            parts = str(uri).rstrip("/").rsplit("/", 1)
            return parts[-1] if parts[-1] else None
    return None


def fill_paths_from_preview(paths: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    """Add columns from preview_rows not already in paths (mutates paths in place)."""
    if not rows:
        return
    existing = {p["path"] for p in paths}
    first_row = rows[0]
    for col in sorted(first_row.keys()):
        if col != "_row_index" and col not in existing:
            sample = first_row[col]
            paths.append({
                "path": col,
                "type": infer_type(sample),
                "label": col.rsplit(".", 1)[-1],
                "sample": sample,
            })
