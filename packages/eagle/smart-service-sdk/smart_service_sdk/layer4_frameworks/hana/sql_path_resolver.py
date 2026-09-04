from collections.abc import Sequence
from importlib import resources
from pathlib import Path
from typing import Any

SqlSource = Any


def _sorted_sql_sources(sql_sources: list[SqlSource]) -> list[SqlSource]:
    return sorted(sql_sources, key=lambda sql_source: getattr(sql_source, "name", ""))


def _bundled_sql_sources(*parts: str) -> list[SqlSource]:
    sql_root = resources.files("smart_service_sdk")
    for part in parts:
        sql_root = sql_root.joinpath(part)
    if not sql_root.is_dir():
        return []
    return _sorted_sql_sources(
        [
            child
            for child in sql_root.iterdir()
            if child.is_file() and child.name.endswith(".sql")
        ]
    )


def _extra_sql_sources(
    extra_sql_paths: Sequence[str | Path] | None,
) -> list[Path]:
    if not extra_sql_paths:
        return []
    return _sorted_sql_sources([Path(sql_path) for sql_path in extra_sql_paths])


def resolve_schema_sql_sources(
    extra_sql_paths: Sequence[str | Path] | None = None,
) -> list[SqlSource]:
    return [
        *_bundled_sql_sources("deployment", "sql"),
        *_extra_sql_sources(extra_sql_paths),
    ]


def resolve_seed_sql_sources(
    extra_sql_paths: Sequence[str | Path] | None = None,
) -> list[SqlSource]:
    return [
        *_bundled_sql_sources("deployment", "seed"),
        *_extra_sql_sources(extra_sql_paths),
    ]
