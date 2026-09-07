import os
from dataclasses import dataclass, field
from typing import List, Optional

import polars as pl

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.storage_interface import IFileStorageProvider


@dataclass(kw_only=True)
class BuildReferenceKeysetCommand:
    source_file_path: str
    source_format: str = "csv"
    # Single-value columns whose cell IS one key (e.g. ["Name", "cid"]).
    key_columns: List[str] = field(default_factory=list)
    # Delimited multi-value columns exploded into many keys (e.g. ["Synonyms"]).
    multi_value_columns: List[str] = field(default_factory=list)
    multi_value_delimiter: str = "|"
    # Optional row-level "is this compound hazardous?" filter applied BEFORE key
    # extraction: keep only rows whose hazard_filter_column contains any keyword.
    hazard_filter_column: Optional[str] = None
    hazard_keywords: Optional[List[str]] = None
    output_key_column: str = "key"


@dataclass(kw_only=True)
class BuildReferenceKeysetResult:
    success: bool
    reference_file_url: str = ""
    key_count: int = 0
    message: str = ""


class BuildReferenceKeysetUseCase:
    """
    Materialize a reference key-set ONCE from a large source file (e.g. the PubChem
    GHS compound CSV) into a compact, de-duplicated parquet of normalized lookup keys.

    This is the expensive O(m) discovery step of the dangerous-substance reference:
    explode delimited synonyms, optionally keep only hazardous rows, normalize
    (lower + trim), de-duplicate. The resulting parquet is small and is what
    ``validate_file``'s ``reference_lookup`` rule semi-joins against — turning each
    later validation into a cheap O(n + m) hash membership instead of an O(n * m)
    per-cell scan of the 500MB+ source.
    """

    def __init__(self, logger: ILogger, storage: IFileStorageProvider):
        self.logger = logger
        self.storage = storage

    async def execute(self, command: BuildReferenceKeysetCommand) -> BuildReferenceKeysetResult:
        self.logger.info(
            f"Building reference key-set from: {command.source_file_path} "
            f"(keys={command.key_columns}, multi={command.multi_value_columns})"
        )

        if not command.key_columns and not command.multi_value_columns:
            return BuildReferenceKeysetResult(
                success=False,
                message="At least one of key_columns or multi_value_columns is required",
            )

        local_path = self.storage.download_to_local(
            command.source_file_path, command.source_format
        )
        downloaded = local_path != command.source_file_path
        key = command.output_key_column

        try:
            # Lazy scan — all columns as text so identifiers keep exact form.
            lf = pl.scan_csv(local_path, infer_schema_length=0, ignore_errors=True)

            # Row-level hazard filter (a row = one compound) BEFORE exploding keys.
            if command.hazard_filter_column and command.hazard_keywords:
                hazard_col = pl.col(command.hazard_filter_column).cast(pl.Utf8)
                cond = None
                for kw in command.hazard_keywords:
                    c = hazard_col.str.contains(kw, literal=True)
                    cond = c if cond is None else (cond | c)
                if cond is not None:
                    lf = lf.filter(cond.fill_null(False))

            parts: List[pl.LazyFrame] = []
            for col in command.key_columns:
                parts.append(lf.select(pl.col(col).cast(pl.Utf8).alias(key)))
            for col in command.multi_value_columns:
                parts.append(
                    lf.select(
                        pl.col(col)
                        .cast(pl.Utf8)
                        .str.split(command.multi_value_delimiter)
                        .alias(key)
                    ).explode(key)
                )

            combined = pl.concat(parts, how="vertical")
            normalized = (
                combined.select(
                    pl.col(key).cast(pl.Utf8).str.strip_chars().str.to_lowercase().alias(key)
                )
                .filter(pl.col(key).is_not_null() & (pl.col(key) != ""))
                .unique()
            )

            # Stream the heavy explode/normalize pipeline; the de-duplicated result
            # is small enough to materialize.
            df = normalized.collect(streaming=True)
            key_count = df.height

            # solace storage's upload_dataframe returns a StoredFileInfo (not a bare
            # URL string). Downstream, reference_lookup resolves the key-set via
            # storage.download_and_read(<file_id>), so expose the file_id as the
            # reference source identifier.
            stored = self.storage.upload_dataframe(df, "parquet")
            reference_url = stored.file_id
            self.logger.info(
                f"Reference key-set built: {key_count} distinct keys -> {reference_url}"
            )
            return BuildReferenceKeysetResult(
                success=True,
                reference_file_url=reference_url,
                key_count=key_count,
                message=f"Built reference key-set with {key_count} distinct keys",
            )
        except Exception as e:
            self.logger.error(f"build_reference_keyset error: {e}")
            return BuildReferenceKeysetResult(success=False, message=str(e))
        finally:
            if downloaded and os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except OSError:
                    pass
