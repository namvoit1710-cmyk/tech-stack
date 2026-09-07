"""Reading a HANA table into Polars without losing digits.

Polars infers a Decimal's scale from the first `infer_schema_length` rows. On SAP
data that is wrong often enough to matter: MARA carries 48 scaled DECIMAL columns and
most rows hold whole numbers, so a sample of 100 sees no fraction and the column is
inferred at scale 0. Every fractional weight is then silently rounded - and on the
migration path those rounded values are what gets written to the target system.

The driver already knows the truth. `cursor.description` carries `(name, type_code,
display_size, internal_size, precision, scale, null_ok)` for every column, so the
schema is pinned from the metadata and never guessed. `assert_no_precision_loss`
re-checks the materialised frame afterwards and raises rather than let a truncated
column through quietly.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

import polars as pl

logger = logging.getLogger(__name__)

# hdbcli type codes. 5 is DECIMAL/SMALLDECIMAL - the only ones carrying a scale we
# have to preserve by hand.
DECIMAL_CODES = {5}

MAX_DECIMAL_PRECISION = 38


def schema_from_description(
    description: Sequence[tuple], decimals_as: str = "decimal"
) -> dict[str, Optional[pl.DataType]]:
    """Column -> dtype, from the driver's metadata.

    A `None` value means "safe to infer": only the scaled decimals are pinned, so
    everything else keeps Polars' own type handling.
    """
    schema: dict[str, Optional[pl.DataType]] = {}
    for name, type_code, _display, _internal, precision, scale, _null_ok in description:
        if type_code in DECIMAL_CODES and scale is not None and int(scale) > 0:
            if decimals_as == "utf8":
                schema[name] = pl.Utf8
            elif decimals_as == "float":
                schema[name] = pl.Float64
            else:
                schema[name] = pl.Decimal(
                    min(int(precision or MAX_DECIMAL_PRECISION), MAX_DECIMAL_PRECISION),
                    int(scale),
                )
        else:
            schema[name] = None
    return schema


def assert_no_precision_loss(description: Sequence[tuple], df: pl.DataFrame) -> None:
    """Raise if the frame holds fewer decimal places than HANA declared.

    Cheap, and it turns a silent data-corruption bug into a loud startup error. This
    check is the difference between shipping 26.5 and shipping 27.
    """
    for name, type_code, _d, _i, precision, scale, _n in description:
        if type_code not in DECIMAL_CODES or not scale or name not in df.columns:
            continue
        dtype = df.schema[name]
        held = getattr(dtype, "scale", None)
        if held is not None and int(held) < int(scale):
            raise ValueError(
                f"column {name!r} is DECIMAL(*,{int(scale)}) in HANA but was "
                f"materialised as {dtype} - {int(scale) - int(held)} decimal place(s) "
                f"would be lost. Pin the schema from cursor.description."
            )


def fetch_dataframe(
    cursor: Any,
    sql: str,
    *,
    decimals_as: str = "decimal",
    chunk: int = 50_000,
) -> pl.DataFrame:
    """Execute and materialise, with the schema pinned and then verified."""
    cursor.execute(sql)
    description = cursor.description
    schema = schema_from_description(description, decimals_as)

    rows: list[tuple] = []
    while True:
        batch = cursor.fetchmany(chunk)
        if not batch:
            break
        rows.extend(tuple(r) for r in batch)

    df = pl.DataFrame(rows, schema=schema, orient="row")
    assert_no_precision_loss(description, df)
    logger.info("read %d rows x %d columns from HANA", df.height, df.width)
    return df


def connect(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    encrypt: bool = True,
    validate_cert: bool = False,
    connect_timeout_ms: int = 30_000,
    communication_timeout_ms: int = 600_000,
):
    """A HANA Cloud connection. `hdbcli` is imported here so the rest of the app
    still starts on a host that has no SAP driver installed."""
    from hdbcli import dbapi

    return dbapi.connect(
        address=host,
        port=int(port),
        user=user,
        password=password,
        encrypt=bool(encrypt),
        sslValidateCertificate=bool(validate_cert),
        connectTimeout=connect_timeout_ms,
        communicationTimeout=communication_timeout_ms,
    )
