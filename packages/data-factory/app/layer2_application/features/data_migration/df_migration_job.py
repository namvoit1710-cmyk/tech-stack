"""The DB-source migration path: IH dispatches, DF validates in place, IH reads tables.

Integration Hub already builds a dispatch - `{job_id, source{connection, credential,
virtual_table}, rules[], callback{url, job_id}}` - and until now had nowhere to send
it. This is the receiving end.

WHY THE WORK IS SPLIT THE WAY IT IS

The rules are DF's own Polars rules and must keep DF's exact semantics. But the
source is a 307-column SAP virtual table; materialising it whole in Polars costs
~1.4 GB and pushing the result back over HTTP costs ~216 MB of JSON per run. So:

  0. get the connection secret WITHOUT asking anyone              (see resolve_password)
  1. read ONLY the columns the rules mention, plus the DDIC key   (narrow, cheap)
  2. evaluate with DF's OWN handlers - RuleFactory, the transformer, and the
     validator's whole-table pass for unique/set_unique          (exact semantics)
  3. push a narrow verdict table: key + summary + rule-added columns
  4. build the wide report IN-DATABASE by joining source to verdict
  5. fill the delivery table from the report in committed chunks (visible live)
  6. ack IH at callback.url

Polars decides; SQL moves the bytes. Neither does the other's job.

TWO TABLES, BOTH NAMED FROM job_id

  DF_REPORT_<job>  every row, every source column, plus IS_VALID / ERROR_COUNT /
                   ERROR_RULES / ERROR_DETAIL and per-row provenance. A fixed width
                   whatever the rule count - a flag column per rule would add one
                   column per rule and still not say which value was wrong.
  DF_CB_<job>      data columns only, and only rows that passed every rule.

The names are pure functions of `job_id`, which IH generated, so IH can find them
without being told - which matters, because it cannot be told: IH's DfCallbackDto has
four fields and Pydantic drops unknown keys silently, so a table name posted in the
callback vanishes with no error.

NO RUNTIME DEPENDENCY ON INTEGRATION HUB

Neither leg to IH is required. The secret comes from the dispatch, a bound service
instance or DF's own configuration, and only falls back to IH's token exchange if
none of those is present (`resolve_password`). The closing ack is a notification, not
the delivery - it has its own error handling and cannot fail a finished migration.

So a migration runs to completion with IH switched off entirely: the tables are
written, and IH can collect them from `job_id` whenever it comes back.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
import polars as pl
from pydantic import BaseModel, Field

from app.layer1_domain.entities.transformation import TransformRule
from app.layer1_domain.entities.validation import ValidationRule
from app.layer4_frameworks.providers.adaptive_batching import _get_system_memory
from app.layer4_frameworks.providers.hana import credentials as hana_credentials
from app.layer4_frameworks.providers.hana import hana_reader
from app.layer4_frameworks.providers.transformation.polars_transformer_provider import (
    PolarsTransformerProvider,
)
from app.layer4_frameworks.providers.validation.polars_validator_provider import (
    PolarsValidatorProvider,
)
from app.layer4_frameworks.providers.validation.rule_handlers import RuleFactory

logger = logging.getLogger(__name__)

CHUNK_ROWS = 5_000
DEFAULT_KEY = ("MANDT", "MATNR")

TRANSFORM_TYPES = {
    "filter", "mapping", "drop_columns", "rename_columns", "case_when_expr",
    "insert_row", "delete_row", "update_row",
}
GLOBAL_TYPES = {"unique", "set_unique"}


# --------------------------------------------------------------------- contract
class DfConnectionDto(BaseModel):
    host: str
    port: int = 443
    user: str
    password: Optional[str] = None      # optional; see resolve_password()
    schema_: str = Field(default="", alias="schema")
    encrypt: bool = True
    validate_cert: bool = False

    model_config = {"populate_by_name": True}


class DfCredentialDto(BaseModel):
    """Optional. Only used when DF cannot get the secret on its own."""

    scheme: str = "resolve-token"
    token: Optional[str] = None
    resolve_url: Optional[str] = None


class DfSourceDto(BaseModel):
    kind: str = "hana_virtual_table"
    connection: DfConnectionDto
    credential: Optional[DfCredentialDto] = None
    virtual_table: str


class DfRuleDto(BaseModel):
    type: str
    params: dict = Field(default_factory=dict)
    rule_name: Optional[str] = None
    error_message: Optional[str] = None


class DfCallbackTargetDto(BaseModel):
    url: str
    job_id: str


class DispatchDto(BaseModel):
    """Exactly what IH's StartRuleMigrationJobUseCase emits. Do not reshape it."""

    job_id: str
    source: DfSourceDto
    rules: list[DfRuleDto] = Field(default_factory=list)
    callback: DfCallbackTargetDto


# ------------------------------------------------------------------------- job
@dataclass
class MigrationJob:
    job_id: str
    #: ACCEPTED WAITING READING VALIDATING WRITING COMPLETED FAILED
    #: WAITING means admitted-but-held: the container is over its memory ceiling and
    #: this job is queued at the door rather than adding to it.
    status: str = "ACCEPTED"
    report_table: str = ""
    callback_table: str = ""
    #: The delivery table's data columns, and the job_id IH asked to be called back
    #: on. Both live here so the terminal message can be built without the dispatch:
    #: the SSE stream holds the job, not the dispatch, and must not be able to send
    #: a different message from the POST.
    columns: list = field(default_factory=list)
    ack_job_id: str = ""
    rows_read: int = 0
    rows_passed: int = 0
    rows_written: int = 0
    rules_applied: int = 0
    rules_violated: int = 0
    error_rows: int = 0
    violations: dict = field(default_factory=dict)
    credential_source: str = ""
    acked: bool = False
    ack_error: Optional[str] = None
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def as_dict(self) -> dict:
        pct = (self.rows_written / self.rows_passed * 100) if self.rows_passed else 0.0
        return {
            "jobId": self.job_id,
            "status": self.status,
            "reportTable": self.report_table,
            "callbackTable": self.callback_table,
            "rowsRead": self.rows_read,
            "rowsPassed": self.rows_passed,
            "rowsWritten": self.rows_written,
            "progressPct": round(pct, 1),
            "rulesApplied": self.rules_applied,
            "rulesViolated": self.rules_violated,
            "errorRows": self.error_rows,
            "violations": self.violations,
            "credentialSource": self.credential_source,
            "ackedToIh": self.acked,
            "ackError": self.ack_error,
            "error": self.error,
            "elapsedSec": round((self.finished_at or time.time()) - self.started_at, 2),
        }


def ack_payload(job: MigrationJob) -> dict:
    """The one terminal message, whether it is POSTed to IH or pushed down the stream.

    `rows` is empty because the rows are in the table. `columns` must NOT be: IH sizes
    the target from it, so an empty list would create the target without any rule-added
    column. Both senders call this, so the two can never drift apart.
    """
    return {
        "jobId": job.ack_job_id or job.job_id,
        "columns": list(job.columns),
        "rows": [],
        "done": True,
    }


class MigrationJobStore:
    """In-memory, single instance. A restart loses job status; the tables survive,
    which is the point of writing them to the database in the first place."""

    def __init__(self) -> None:
        self._jobs: dict[str, MigrationJob] = {}
        self._lock = threading.Lock()

    def put(self, job: MigrationJob) -> None:
        with self._lock:
            self._jobs[job.job_id] = job

    def get(self, job_id: str) -> Optional[MigrationJob]:
        return self._jobs.get(job_id)


def report_table_name(job_id: str) -> str:
    return f"DF_REPORT_{job_id.replace('-', '').upper()}"


def callback_table_name(job_id: str) -> str:
    return f"DF_CB_{job_id.replace('-', '').upper()}"


# ------------------------------------------------------------------ rule support
_COL_RE = re.compile(r"""pl\.col\(\s*['"]([^'"]+)['"]\s*\)""")


def columns_touched(rules: list[DfRuleDto]) -> set[str]:
    """Every column any rule reads.

    Reading only these is what keeps the frame small - but a column that a rule
    needs and we never read does not fail loudly, it fails *wrongly*. So this looks
    everywhere a column name can hide: `columns`, compare_fields' `left`/`right`,
    conditional_required's `when.column` and `then`, and any `pl.col('X')` inside an
    expression.
    """
    found: set[str] = set()
    for rule in rules:
        params = rule.params or {}
        for key in ("columns", "columns_to_drop"):
            found.update(str(c) for c in (params.get(key) or []))
        for key in ("left", "right"):
            if params.get(key):
                found.add(str(params[key]))
        when = params.get("when")
        if isinstance(when, dict) and when.get("column"):
            found.add(str(when["column"]))
        then = params.get("then")
        if isinstance(then, str):
            found.add(then)
        elif isinstance(then, (list, tuple)):
            found.update(str(c) for c in then)
        found.update(_COL_RE.findall(json.dumps(params)))
    return found


def columns_produced(rules: list[DfRuleDto]) -> set[str]:
    made: set[str] = set()
    for rule in rules:
        for key in ("column_dest", "new_col"):
            if (rule.params or {}).get(key):
                made.add(str(rule.params[key]))
    return made


def split_rules(rules: list[DfRuleDto]):
    """transformations / row-wise validations / whole-table validations."""
    factory = RuleFactory()
    transforms, row_rules, global_rules = [], [], []
    for rule in rules:
        if rule.type in TRANSFORM_TYPES:
            transforms.append(rule)
        elif rule.type in GLOBAL_TYPES:
            global_rules.append(rule)
        elif factory.get_handler(rule.type):
            row_rules.append(rule)
        else:
            raise ValueError(
                f"unsupported rule type {rule.type!r}. DF handles "
                f"{sorted(TRANSFORM_TYPES)} as transformations, "
                f"{sorted(factory.handlers)} as row validations, and "
                f"{sorted(GLOBAL_TYPES)} as whole-table validations."
            )
    return transforms, row_rules, global_rules


class _RuleLogger:
    def info(self, message, *args, **kwargs):
        logger.info(str(message))

    def debug(self, *args, **kwargs):
        pass

    warning = error = critical = debug


def _as_validation_rule(rule: DfRuleDto, index: int) -> ValidationRule:
    name = rule.rule_name or f"{rule.type}_{index}"
    return ValidationRule(
        rule_name=name,
        type=rule.type,
        params=rule.params,
        error_message=rule.error_message or f"{name} failed",
    )


def apply_rules(df: pl.DataFrame, transforms, row_rules, global_rules):
    """DF's own handlers, all three kinds.

    Returns `(frame after transforms, struct frame)`. Each column of the struct frame
    is one guarded column of one rule; a non-null entry IS a violation and already
    carries rule / field / path / message / value. That is the error log, assembled
    by DF - nothing is re-derived here.
    """
    transformer = PolarsTransformerProvider(_RuleLogger(), None)
    for rule in transforms:
        df = transformer._apply_dataframe_rule(
            df,
            TransformRule(
                rule_name=rule.rule_name or rule.type,
                type=rule.type,
                params=rule.params,
            ),
        )

    factory = RuleFactory()
    exprs = []
    for index, rule in enumerate(row_rules):
        handler = factory.get_handler(rule.type)
        exprs.extend(handler.parse_rule(_as_validation_rule(rule, index), alias=f"R{index}"))

    if global_rules:
        # unique / set_unique need the whole column before they can say which values
        # repeat, so DF keeps them on the validator provider rather than in
        # RuleFactory. Reuse it: duplicate detection has to be DF's semantics, not a
        # second implementation free to drift from it.
        provider = PolarsValidatorProvider(_RuleLogger(), None)
        global_exprs, _ = provider._process_global_rules(
            df, [_as_validation_rule(r, 1000 + i) for i, r in enumerate(global_rules)]
        )
        exprs.extend(global_exprs)

    if not exprs:
        return df, pl.DataFrame()
    return df, df.select(exprs)


def _summarise(struct: pl.DataFrame) -> pl.DataFrame:
    """Per-row ERROR_COUNT / ERROR_RULES / ERROR_DETAIL from the violation structs."""
    cols = struct.columns
    n_bad = pl.sum_horizontal([pl.col(c).is_not_null().cast(pl.Int32) for c in cols])
    names = pl.concat_list([pl.col(c).struct.field("rule") for c in cols])
    # One readable line per violation: `rule [field=value] message`.
    # Delimited rather than JSON deliberately - a value containing a quote would
    # produce invalid JSON silently, and a log people rely on has to be trustworthy.
    detail = pl.concat_list(
        [
            pl.when(pl.col(c).is_not_null())
            .then(
                pl.concat_str([
                    pl.col(c).struct.field("rule"),
                    pl.lit(" ["),
                    pl.col(c).struct.field("field").cast(pl.Utf8).fill_null(""),
                    pl.lit("="),
                    pl.col(c).struct.field("value").cast(pl.Utf8).fill_null(""),
                    pl.lit("] "),
                    pl.col(c).struct.field("message").cast(pl.Utf8).fill_null(""),
                ])
            )
            .otherwise(None)
            for c in cols
        ]
    )
    return struct.with_row_index("_RID").select(
        "_RID",
        n_bad.alias("ERROR_COUNT"),
        names.list.drop_nulls().list.unique().list.sort().list.join(", ").alias("ERROR_RULES"),
        detail.list.drop_nulls().list.join(" ;; ").alias("ERROR_DETAIL"),
    )


def _column_ddl(frame: pl.DataFrame) -> str:
    """Size text columns from the data. ERROR_RULES is a comma-joined list of every
    rule a row broke, which blows past any fixed 255 as soon as the pack is large."""
    fixed = {pl.Int32: "INT", pl.Int64: "BIGINT", pl.UInt32: "BIGINT"}
    defs = []
    for name, dtype in frame.schema.items():
        sql_type = fixed.get(dtype)
        if sql_type is None:
            widest = int(
                frame[name].cast(pl.Utf8, strict=False).str.len_chars().max() or 1
            )
            sql_type = f"NVARCHAR({min(max(widest + 32, 64), 5000)})"
        defs.append(f'"{name}" {sql_type}')
    return ", ".join(defs)


# ------------------------------------------------------------------- credentials
# The two lookups themselves moved to providers/hana/credentials.py: they are pure
# infrastructure - "what did the platform bind, what did the operator configure" -
# and the semantic-duplication rule needs the same answers. The ORDER below is
# policy and stays here, with the feature that owns it.
_vcap_password = hana_credentials.vcap_password
_configured_password = hana_credentials.configured_password


def resolve_password(dispatch: "DispatchDto") -> tuple[str, str]:
    """The connection secret, and where it came from.

    DF does not depend on Integration Hub for this. It looks, in order, at the
    places that need nobody else to be running:

      1. the dispatch's own connection.password  - caller supplied it outright
      2. a bound service instance (VCAP_SERVICES) - the platform supplied it
      3. DF's own configuration                   - the operator supplied it

    and only then falls back to `credential.resolve_url`, which is IH's single-use
    token exchange. That last one still works, so IH's existing contract is honoured
    unchanged - but it is now a fallback, not a requirement. With any of the first
    three configured, a migration runs with IH switched off entirely.
    """
    conf = dispatch.source.connection
    if conf.password:
        return conf.password, "dispatch"

    bound = _vcap_password(conf.host, conf.user)
    if bound:
        return bound, "service-binding"

    configured = _configured_password(conf.host, conf.user)
    if configured:
        return configured, "df-config"

    credential = dispatch.source.credential
    if credential and credential.resolve_url and credential.token:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                credential.resolve_url,
                json={"jobId": dispatch.job_id, "token": credential.token},
            )
        if response.status_code != 200:
            raise RuntimeError(
                f"could not resolve the connection secret: the resolver answered "
                f"{response.status_code} {response.text[:160]}. That token is "
                f"single-use and short-lived, so a retry needs a fresh dispatch. "
                f"Configure DF_HANA_CREDENTIALS or bind a HANA service instance to "
                f"remove this dependency."
            )
        return response.json()["password"], "resolve-token"

    raise RuntimeError(
        f"no connection secret available for {conf.user}@{conf.host}. Supply one of: "
        f"source.connection.password, a bound HANA service instance, "
        f"DF_HANA_CREDENTIALS / DF_HANA_PASSWORD, or source.credential."
    )


# ------------------------------------------------------- connection reuse (fixed cost)
#: Measured against HANA Cloud over WAN: connect + TLS + auth is ~4.2s, and a short
#: migration is ~21s. Every job was paying a fifth of its runtime before doing any work.
#:
#: POOL_MAX is not a throttle. A job that finds the pool empty dials its own connection
#: and never waits, so concurrency is bounded by the worker threadpool and by memory -
#: not by this. What it bounds is how many connections stay parked once a burst is over:
#: uncapped, 40 concurrent jobs leave 40 idle sessions on a database other things share.
#: Size it to steady-state concurrency, not to peak.
#:
#: An idle connection was measured surviving ~26 min before the far end reset it, so the
#: default TTL sits under that. Setting it optimistically is safe anyway - a connection
#: that died early is caught by the probe in acquire_connection and simply replaced.
_POOL_LOCK = threading.Lock()
_POOL: dict[tuple, list] = {}
POOL_MAX = int(os.environ.get("DF_POOL_MAX", "4"))
POOL_IDLE_SEC = float(os.environ.get("DF_POOL_IDLE_SEC", "1200"))

#: Admission control. Connections were never the resource that runs out first - memory
#: is. Every job holds its columns in a Polars frame inside this one container, so
#: enough concurrent migrations will OOM the app and take every running job down with
#: it, including ones that were nearly finished. A job that waits, or is refused with a
#: reason, costs one migration; an OOM costs all of them.
MEMORY_CEILING_PCT = float(os.environ.get("DF_MEMORY_CEILING_PCT", "80"))
ADMIT_WAIT_SEC = float(os.environ.get("DF_ADMIT_WAIT_SEC", "120"))
ADMIT_POLL_SEC = 2.0


class MemoryPressure(RuntimeError):
    """Refused before starting, because starting would have risked the whole app."""


def memory_used_pct() -> float:
    """Container-aware: in CF, psutil reports the host, not the 9GB we actually get."""
    return 100.0 - _get_system_memory()["available_percent"]


def wait_for_memory(job) -> None:
    """Hold a job at the door while the app is over its ceiling.

    This is admission control, not enforcement: it cannot stop memory rising *during*
    a job it already admitted, only stop piling more on top of a container that is
    already close to the edge. Deliberately checked before connecting, so a job that
    waits holds no connection while it does.
    """
    deadline = time.monotonic() + ADMIT_WAIT_SEC
    waited = False
    while True:
        used = memory_used_pct()
        if used < MEMORY_CEILING_PCT:
            if waited:
                logger.info("migration %s: admitted at %.1f%% memory", job.job_id, used)
            return
        if time.monotonic() >= deadline:
            raise MemoryPressure(
                f"memory is at {used:.1f}%, over the {MEMORY_CEILING_PCT:.0f}% ceiling, "
                f"after waiting {ADMIT_WAIT_SEC:.0f}s. Refusing to start rather than "
                f"risk an OOM that would fail every migration running now."
            )
        if not waited:
            waited = True
            job.status = "WAITING"
            logger.warning(
                "migration %s: memory at %.1f%% >= %.0f%%, waiting up to %.0fs",
                job.job_id, used, MEMORY_CEILING_PCT, ADMIT_WAIT_SEC,
            )
        time.sleep(ADMIT_POLL_SEC)

#: `SELECT * LIMIT 0` on a federated table costs 3-5s and returns names that rarely
#: change. Short TTL on purpose: a virtual table does get rebuilt against a changed
#: source - it happened to STAGING_VT_MARA - and a stale list would let a rule name a
#: column that is gone, failing later and deeper with a worse message than the upfront
#: check gives.
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_CACHE: dict[tuple, tuple] = {}
SCHEMA_TTL_SEC = 300.0


def _pool_key(conf) -> tuple:
    return (conf.host, conf.port, conf.user,
            bool(conf.encrypt), bool(conf.validate_cert))


def _discard(connection) -> None:
    try:
        connection.close()
    except Exception:
        pass


def _usable(connection) -> bool:
    """A parked connection can die while idle - server restart, dropped NAT. Cheaper
    to ask than to fail a job on its first real statement."""
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT 1 FROM DUMMY")
            cursor.fetchone()
        finally:
            cursor.close()
        return True
    except Exception:
        return False


def acquire_connection(conf, password):
    """A parked connection if one is healthy, otherwise a new one.

    Checked out exclusively, never shared: a job runs DDL and commits on its own
    transaction, so two jobs on one connection would commit each other's work.
    """
    key = _pool_key(conf)
    while True:
        with _POOL_LOCK:
            idle = _POOL.get(key) or []
            if not idle:
                break
            connection, parked_at = idle.pop()
        if time.time() - parked_at > POOL_IDLE_SEC or not _usable(connection):
            _discard(connection)
            continue
        return connection
    return hana_reader.connect(
        host=conf.host, port=conf.port, user=conf.user, password=password,
        encrypt=conf.encrypt, validate_cert=conf.validate_cert,
    )


def release_connection(conf, connection, reuse: bool) -> None:
    """Park a healthy connection, close a suspect one.

    Never raises. This runs in a finally block, and a failure to hand the connection
    back must not be what decides the job's outcome.
    """
    if connection is None:
        return
    if not reuse:
        _discard(connection)
        return
    try:
        connection.rollback()          # never park one mid-transaction
    except Exception:
        _discard(connection)
        return
    with _POOL_LOCK:
        parked = _POOL.setdefault(_pool_key(conf), [])
        if len(parked) >= POOL_MAX:
            full = True
        else:
            parked.append((connection, time.time()))
            full = False
    if full:
        # A burst is over and the pool already holds its share. Closing the surplus
        # here is the whole point of the cap: nothing queues, we just stop hoarding.
        _discard(connection)


def table_columns(cursor, conf, table: str) -> list:
    """Column names for a table, cached for SCHEMA_TTL_SEC."""
    key = (conf.host, conf.port, conf.user, table)
    with _SCHEMA_LOCK:
        hit = _SCHEMA_CACHE.get(key)
        if hit and time.time() - hit[1] < SCHEMA_TTL_SEC:
            return list(hit[0])
    cursor.execute(f"SELECT * FROM {table} LIMIT 0")
    columns = [d[0] for d in cursor.description]
    with _SCHEMA_LOCK:
        _SCHEMA_CACHE[key] = (list(columns), time.time())
    return columns


def _drop(cursor, connection, table: str) -> None:
    try:
        cursor.execute(f'DROP TABLE "{table}"')
        connection.commit()
    except Exception:
        connection.rollback()


# -------------------------------------------------------------------- the pipeline
def run_migration_job(dispatch: DispatchDto, job: MigrationJob) -> None:
    source = dispatch.source
    connection = None
    staging = ""
    # Only a job that ran to the end hands its connection back. Anything else - an
    # error, a Ctrl-C, a killed worker - closes it, because parking a connection whose
    # state we are no longer sure of just moves the failure into the next job.
    healthy = False
    job.ack_job_id = dispatch.callback.job_id
    try:
        # 0. check the rule set BEFORE spending the single-use token --------------
        # Rejecting a rule set afterwards would burn the token, force IH to mint a
        # whole new job over a typo, and surface as a confusing 403 from
        # /connection-secret rather than the real reason.
        transforms, row_rules, global_rules = split_rules(dispatch.rules)
        job.rules_applied = len(dispatch.rules)

        # 0.5 don't pile onto a container that is already near the edge -------------
        wait_for_memory(job)

        # 1. get the secret -------------------------------------------------------
        job.status = "READING"
        password, job.credential_source = resolve_password(dispatch)
        logger.info(
            "migration %s: credential from %s", dispatch.job_id, job.credential_source
        )

        conf = source.connection
        connection = acquire_connection(conf, password)
        cursor = connection.cursor()
        virtual_table = source.virtual_table

        # 2. narrow read ----------------------------------------------------------
        all_columns = table_columns(cursor, conf, virtual_table)
        key = [k for k in DEFAULT_KEY if k in all_columns] or [all_columns[0]]

        touched = columns_touched(dispatch.rules)
        # Name every bad reference at once. Polars raises on the first one it meets,
        # which on a large rule pack means fixing them one run at a time.
        unknown = sorted(
            c for c in touched
            if c not in all_columns and c not in columns_produced(dispatch.rules)
        )
        if unknown:
            raise ValueError(
                f"{len(unknown)} column(s) referenced by rules do not exist in "
                f"{virtual_table}: {', '.join(unknown)}"
            )

        # One federated scan, then everything local. The source is a virtual table, so
        # every scan of it crosses to SAP - and the job scanned it twice: narrow here,
        # then all of it again for the report join. Copying it once costs a local write
        # and takes the second crossing off the clock.
        staging = f"DF_SRC_{dispatch.job_id.replace('-', '').upper()}"
        _drop(cursor, connection, staging)
        cursor.execute(
            f'CREATE COLUMN TABLE "{staging}" AS (SELECT * FROM {virtual_table})'
        )
        connection.commit()
        local_source = f'"{staging}"'

        wanted = [c for c in dict.fromkeys(list(key) + sorted(touched)) if c in all_columns]
        selection = ", ".join(f'"{c}"' for c in wanted)
        df = hana_reader.fetch_dataframe(
            cursor, f"SELECT {selection} FROM {local_source}"
        )
        job.rows_read = df.height
        logger.info(
            "migration %s: read %d rows x %d of %d columns",
            dispatch.job_id, df.height, df.width, len(all_columns),
        )

        # The report is joined back on the key; a duplicate key would fan it out.
        if df.select(key).n_unique() != df.height:
            raise ValueError(
                f"key {key} is not unique in {virtual_table} - the report join would "
                f"duplicate rows; refusing to continue"
            )

        # 3. evaluate -------------------------------------------------------------
        job.status = "VALIDATING"
        transformed, struct = apply_rules(df, transforms, row_rules, global_rules)
        added = [c for c in transformed.columns if c not in df.columns]

        verdict = transformed.select(key + added).with_row_index("_RID")
        if struct.width:
            verdict = verdict.join(_summarise(struct), on="_RID", how="left")
            job.error_rows = int(verdict["ERROR_COUNT"].sum() or 0)
            counts = (
                struct.unpivot(variable_name="_SLOT", value_name="_V")
                .filter(pl.col("_V").is_not_null())
                .select(pl.col("_V").struct.field("rule").alias("RULE"))
                .group_by("RULE")
                .len()
                .sort("len", descending=True)
            )
            job.violations = {k: int(v) for k, v in counts.iter_rows()}
            job.rules_violated = len(job.violations)
        else:
            verdict = verdict.with_columns(
                pl.lit(0).cast(pl.Int32).alias("ERROR_COUNT"),
                pl.lit("").alias("ERROR_RULES"),
                pl.lit("").alias("ERROR_DETAIL"),
            )
        verdict = verdict.drop("_RID").with_columns(
            (pl.col("ERROR_COUNT") == 0).cast(pl.Int32).alias("IS_VALID")
        )
        job.rows_passed = int(verdict["IS_VALID"].sum())

        # 4. verdict in, report built in-database ---------------------------------
        job.status = "WRITING"
        scratch = f"DF_VERDICT_{dispatch.job_id.replace('-', '').upper()}"
        job.report_table = report_table_name(dispatch.job_id)
        job.callback_table = callback_table_name(dispatch.job_id)
        for table in (job.callback_table, job.report_table, scratch):
            try:
                cursor.execute(f'DROP TABLE "{table}"')
                connection.commit()
            except Exception:
                connection.rollback()

        cursor.execute(f'CREATE COLUMN TABLE "{scratch}" ({_column_ddl(verdict)})')
        names = ", ".join(f'"{c}"' for c in verdict.columns)
        marks = ", ".join("?" for _ in verdict.columns)
        cursor.executemany(
            f'INSERT INTO "{scratch}" ({names}) VALUES ({marks})',
            [
                tuple(v if v is None or isinstance(v, (int, float)) else str(v) for v in row)
                for row in verdict.iter_rows()
            ],
        )
        connection.commit()

        on_key = " AND ".join(f'v."{k}" = s."{k}"' for k in key)
        carried = ", ".join(f'v."{c}"' for c in verdict.columns if c not in key)
        provenance = (
            f"'{dispatch.job_id}' AS \"DF_JOB_ID\", "
            f"'DATA_FACTORY' AS \"DF_CREATED_BY\", "
            f"CURRENT_UTCTIMESTAMP AS \"DF_CREATED_AT\", "
            f"'{virtual_table}' AS \"DF_SOURCE\", "
            f"{len(dispatch.rules)} AS \"DF_RULE_COUNT\""
        )
        cursor.execute(
            f'CREATE COLUMN TABLE "{job.report_table}" AS '
            f'(SELECT s.*, {carried}, {provenance} FROM {local_source} s '
            f'JOIN "{scratch}" v ON {on_key})'
        )
        connection.commit()

        # 5. fill the delivery table in committed chunks --------------------------
        data_columns = list(all_columns) + added
        job.columns = data_columns   # the terminal message is built from this
        column_list = ", ".join(f'"{c}"' for c in data_columns)
        cursor.execute(
            f'CREATE COLUMN TABLE "{job.callback_table}" AS '
            f'(SELECT {column_list} FROM "{job.report_table}" WHERE 1 = 0)'
        )

        def stamp(written: int, state: str) -> None:
            """Progress in the table's own catalog comment, committed with the rows,
            so a reader can render a percentage from one catalog query and the two
            can never disagree."""
            comment = json.dumps(
                {
                    "job_id": dispatch.job_id,
                    "status": state,
                    "rows_expected": job.rows_passed,
                    "rows_written": written,
                    "report_table": job.report_table,
                    "by": "DATA_FACTORY",
                },
                separators=(",", ":"),
            )
            cursor.execute(f"COMMENT ON TABLE \"{job.callback_table}\" IS '{comment}'")

        stamp(0, "WRITING")
        connection.commit()

        order_by = ", ".join(f'"{k}"' for k in key)
        offset = 0
        while True:
            cursor.execute(
                f'INSERT INTO "{job.callback_table}" ({column_list}) '
                f'SELECT {column_list} FROM '
                f'(SELECT {column_list}, ROW_NUMBER() OVER (ORDER BY {order_by}) AS RN '
                f'FROM "{job.report_table}" WHERE "IS_VALID" = 1) '
                f"WHERE RN > {offset} AND RN <= {offset + CHUNK_ROWS}"
            )
            written = cursor.rowcount or 0
            job.rows_written += max(written, 0)
            stamp(job.rows_written, "WRITING")
            connection.commit()
            offset += CHUNK_ROWS
            if written <= 0:
                break
        stamp(job.rows_written, "COMPLETED")
        connection.commit()

        # The two tables the job promised are committed; these were only scaffolding.
        _drop(cursor, connection, scratch)
        _drop(cursor, connection, staging)

        # 6. ack IH ---------------------------------------------------------------
        # The same message the SSE stream ends with - see ack_payload().
        #
        # This is deliberately NOT allowed to fail the job. By the time we get here
        # both tables are written, committed and stamped COMPLETED; if IH happens to
        # be down, the migration has still succeeded and saying FAILED would be a lie
        # about the data. The ack is a notification, not the delivery - the delivery
        # is the table, and IH can find it from job_id whenever it comes back.
        try:
            with httpx.Client(timeout=60) as client:
                response = client.post(dispatch.callback.url, json=ack_payload(job))
                job.acked = response.status_code in (200, 202)
                if not job.acked:
                    job.ack_error = f"IH answered {response.status_code} {response.text[:160]}"
                logger.info(
                    "migration %s: acked IH %s %s",
                    dispatch.job_id, response.status_code, response.text[:200],
                )
        except Exception as exc:  # noqa: BLE001 - the data is already delivered
            job.acked = False
            job.ack_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "migration %s: tables are written but IH could not be acked: %s",
                dispatch.job_id, exc,
            )

        job.status = "COMPLETED"
        healthy = True
    except Exception as exc:  # noqa: BLE001 - recorded on the job; the caller polls
        logger.exception("migration job %s failed", dispatch.job_id)
        job.status = "FAILED"
        job.error = f"{type(exc).__name__}: {exc}"
    finally:
        job.finished_at = time.time()
        if connection is not None:
            # A failed job must not leave its scaffolding behind either.
            if staging:
                try:
                    _drop(connection.cursor(), connection, staging)
                except Exception:
                    pass
            release_connection(source.connection, connection, reuse=healthy)
