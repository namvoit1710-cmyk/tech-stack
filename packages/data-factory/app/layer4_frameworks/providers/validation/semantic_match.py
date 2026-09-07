"""Near-duplicate detection by meaning, for the cases blocking-keys cannot reach.

`fuzzy_unique` collapses formatting - case, spacing, punctuation - and then asks the
ordinary duplicate question. That is O(n) and it is why it scales, but the key is a
string, so a word that is spelled differently produces a different key:

    TIE,CABLE,LOCKING,7.31" LG X 0.184"          -> TIECABLELOCKING731LGX0184
    tie,cabl,   self-LOCKING,  7.31 LG X 0.184   -> TIECABLSELFLOCKING731LGX0184

Two spellings of one cable tie, and `fuzzy_unique` reports neither. Measured on the
same pair, HANA's own embedding scores them 0.8960 - so the information is there, it
just is not reachable from a blocking key.

WHERE THE WORK HAPPENS, AND WHY

In the database. HANA Cloud has `VECTOR_EMBEDDING` and `COSINE_SIMILARITY` natively
(confirmed on 4.00.000.00.1786439250), so no embedding service is called, no vector
leaves the tenant, and nothing crosses the egress boundary. It also means the corpus
never has to be pulled into this process: a 11k-row reference stays where it is.

NOTHING HERE IS HARDCODED

The model name, the threshold, the minimum length, and every part of the reference -
schema, table, column, key, filter - arrive in the rule. The engine holds no
knowledge of any tenant's tables. Identifiers are validated and quoted rather than
interpolated raw, because they come from a request body.

COST

Embedding is the expensive half and it is paid per *distinct* value, not per row.
Against a reference whose embeddings are already stored the query is a scan; against
one computed on the fly it is 11k embeddings per query - measured at ~161 s for a
single row against `PRD_MODEL_FINAL_PRODUCTDESCRIPTION`. That is why
`embedding_column` exists, and why its absence is reported rather than silently
tolerated.
"""
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

#: SAP's built-in document embedding model. Overridable per rule; a tenant on a
#: different HANA revision will have a different one, and the engine must not assume.
DEFAULT_MODEL = "SAP_NEB.20240715"

#: Below this, a value carries too little signal for cosine to mean anything: two
#: three-character codes score high against each other for no useful reason.
DEFAULT_MIN_LENGTH = 4

DEFAULT_THRESHOLD = 0.85

#: A plain SQL identifier. Reference names arrive in the request body, so they are
#: checked against this and quoted - never interpolated as given.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")

#: How many distinct values one query will carry. Past this the statement gets long
#: enough to hurt parse time, so callers batch.
MAX_VALUES_PER_QUERY = 500


class SemanticRuleError(ValueError):
    """A semantic rule whose params cannot be turned into a safe query."""


def quote_ident(name: str, what: str) -> str:
    text = str(name or "").strip()
    if not _IDENT.match(text):
        raise SemanticRuleError(
            f"{what} '{name}' is not a plain identifier. Letters, digits and "
            "underscores only - a reference name is used to build SQL and is never "
            "interpolated unchecked."
        )
    return f'"{text.upper()}"'


@dataclass(frozen=True)
class Connection:
    """Which HANA, named by the caller.

    DF may hold the secret - the platform binds it, or the operator configures it -
    but it must never decide *which* database that secret opens. The payload names
    the host, the user and the schema; DF supplies only the password, and reports
    which of its sources it came from. Without that split, a rule pointed at one
    tenant could be answered by another.
    """

    host: str
    user: str
    port: int = 443
    encrypt: bool = True
    validate_cert: bool = False
    #: Optional. When absent the password is resolved from the binding or config.
    password: Optional[str] = None

    @classmethod
    def parse(cls, raw: Any) -> "Connection":
        if not isinstance(raw, dict):
            raise SemanticRuleError("'reference.connection' must be an object")
        missing = [k for k in ("host", "user") if not raw.get(k)]
        if missing:
            raise SemanticRuleError(
                f"'reference.connection' is missing {', '.join(missing)}. The payload "
                "names which HANA and which user; DF supplies only the password."
            )
        try:
            port = int(raw.get("port") or 443)
        except (TypeError, ValueError):
            raise SemanticRuleError(f"'reference.connection.port' must be a number, "
                                    f"got {raw.get('port')!r}")
        return cls(host=str(raw["host"]), user=str(raw["user"]), port=port,
                   encrypt=bool(raw.get("encrypt", True)),
                   validate_cert=bool(raw.get("validate_cert", False)),
                   password=raw.get("password"))


@dataclass(frozen=True)
class Reference:
    """The corpus a value is compared against, named entirely by the caller."""

    schema: str
    table: str
    column: str
    key_column: Optional[str] = None
    embedding_column: Optional[str] = None
    #: A literal predicate, e.g. "MDGSTATUS IN ('REVIEW','PROCESSING')". SMDG's own
    #: duplication search restricts to open change requests exactly this way.
    filter: Optional[str] = None
    connection: Optional[Connection] = None

    @classmethod
    def parse(cls, raw: Any) -> "Reference":
        if not isinstance(raw, dict):
            raise SemanticRuleError("'reference' must be an object")
        missing = [k for k in ("schema", "table", "column") if not raw.get(k)]
        if missing:
            raise SemanticRuleError(
                f"'reference' is missing {', '.join(missing)}. A reference names the "
                "corpus in full so the engine holds no tenant tables of its own."
            )
        return cls(
            schema=raw["schema"], table=raw["table"], column=raw["column"],
            key_column=raw.get("key_column"),
            embedding_column=raw.get("embedding_column"),
            filter=raw.get("filter"),
            connection=Connection.parse(raw["connection"]) if raw.get("connection") else None,
        )

    def qualified(self) -> str:
        return f'{quote_ident(self.schema, "reference.schema")}.' \
               f'{quote_ident(self.table, "reference.table")}'


@dataclass(frozen=True)
class SemanticSpec:
    columns: Sequence[str]
    threshold: float = DEFAULT_THRESHOLD
    min_length: int = DEFAULT_MIN_LENGTH
    model: str = DEFAULT_MODEL
    reference: Optional[Reference] = None

    @classmethod
    def parse(cls, params: Dict[str, Any]) -> "SemanticSpec":
        columns = params.get("columns") or []
        if not columns:
            raise SemanticRuleError("'columns' names no column")

        threshold = params.get("threshold", DEFAULT_THRESHOLD)
        try:
            threshold = float(threshold)
        except (TypeError, ValueError):
            raise SemanticRuleError(f"'threshold' must be a number, got {threshold!r}")
        if not 0.0 < threshold <= 1.0:
            # 0 would match everything and 1 only exact matches, which is `unique`.
            raise SemanticRuleError(
                f"'threshold' must be greater than 0 and at most 1, got {threshold}"
            )

        min_length = params.get("min_length", DEFAULT_MIN_LENGTH)
        try:
            min_length = int(min_length)
        except (TypeError, ValueError):
            raise SemanticRuleError(f"'min_length' must be an integer, got {min_length!r}")
        if min_length < 1:
            raise SemanticRuleError("'min_length' must be at least 1")

        model = str(params.get("model") or DEFAULT_MODEL)
        if "'" in model:
            raise SemanticRuleError(f"'model' contains a quote: {model!r}")

        reference = params.get("reference")
        return cls(
            columns=list(columns), threshold=threshold, min_length=min_length,
            model=model, reference=Reference.parse(reference) if reference else None,
        )


def candidate_values(values: Sequence[Any], min_length: int) -> List[str]:
    """The distinct values worth embedding.

    Blanks are excluded on purpose: a blank is not a near-duplicate of another blank.
    That is `required`'s business, and reporting it here would flag every incomplete
    row a second time under a rule that is not about completeness. Order is kept
    stable so a query - and a test - is reproducible.
    """
    seen, out = set(), []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if len(text) < min_length or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _values_cte(count: int) -> str:
    """`count` placeholders as a one-column table. UNION ALL over DUMMY is the
    portable way to build a values list in HANA without a temp table - and a temp
    table is exactly what a read-only credential cannot create."""
    return " UNION ALL ".join("SELECT ? AS VAL FROM DUMMY" for _ in range(count))


def within_file_sql(spec: SemanticSpec, count: int) -> str:
    """Every pair of values in this file that mean the same thing.

    `a.VAL < b.VAL` is what makes each unordered pair appear once and drops the
    self-pair; without it every value is trivially its own duplicate at 1.0.
    """
    return (
        f"WITH v AS ({_values_cte(count)}), "
        f"e AS (SELECT VAL, VECTOR_EMBEDDING(VAL, 'DOCUMENT', '{spec.model}') AS EMB FROM v) "
        "SELECT a.VAL, b.VAL, COSINE_SIMILARITY(a.EMB, b.EMB) AS SCORE "
        "FROM e a JOIN e b ON a.VAL < b.VAL "
        "WHERE COSINE_SIMILARITY(a.EMB, b.EMB) >= ? "
        "ORDER BY SCORE DESC"
    )


def reference_sql(spec: SemanticSpec, count: int) -> str:
    """Each value against the corpus, best match first."""
    ref = spec.reference
    column = quote_ident(ref.column, "reference.column")
    key = quote_ident(ref.key_column, "reference.key_column") if ref.key_column else "NULL"
    if ref.embedding_column:
        # Stored vectors: a scan. This is the difference between ~0.3 s and ~161 s.
        ref_emb = f'r.{quote_ident(ref.embedding_column, "reference.embedding_column")}'
    else:
        ref_emb = f"VECTOR_EMBEDDING(r.{column}, 'DOCUMENT', '{spec.model}')"
    where = f"COSINE_SIMILARITY(e.EMB, {ref_emb}) >= ?"
    if ref.filter:
        where = f"({ref.filter}) AND {where}"
    return (
        f"WITH v AS ({_values_cte(count)}), "
        f"e AS (SELECT VAL, VECTOR_EMBEDDING(VAL, 'DOCUMENT', '{spec.model}') AS EMB FROM v) "
        f"SELECT e.VAL, {key} AS MATCH_KEY, r.{column} AS MATCH_VALUE, "
        f"COSINE_SIMILARITY(e.EMB, {ref_emb}) AS SCORE "
        f"FROM e, {ref.qualified()} r "
        f"WHERE {where} "
        "ORDER BY SCORE DESC"
    )


@dataclass
class Match:
    value: str
    matched: str
    score: float
    key: Optional[str] = None


@dataclass
class SemanticMatcher:
    """Runs the query and reduces it to 'which values have a near-match, and to what'.

    The executor is injected - `(sql, params) -> rows` - so the matching logic is
    testable without a database, and so the caller decides which connection this runs
    against rather than the engine assuming one.
    """

    spec: SemanticSpec
    execute: Callable[[str, Sequence[Any]], Sequence[Sequence[Any]]]
    warnings: List[str] = field(default_factory=list)

    def find(self, values: Sequence[Any]) -> Dict[str, Match]:
        candidates = candidate_values(values, self.spec.min_length)
        if len(candidates) < (1 if self.spec.reference else 2):
            # Nothing to compare: one value cannot duplicate itself within a file.
            return {}
        if self.spec.reference and not self.spec.reference.embedding_column:
            self.warnings.append(
                "reference has no embedding_column, so every row of the corpus is "
                "embedded on each query. Measured at ~161 s against an 11k-row "
                "corpus. Materialise the vectors before using this at size."
            )

        best: Dict[str, Match] = {}
        for start in range(0, len(candidates), MAX_VALUES_PER_QUERY):
            batch = candidates[start:start + MAX_VALUES_PER_QUERY]
            if self.spec.reference:
                sql = reference_sql(self.spec, len(batch))
                rows = self.execute(sql, [*batch, self.spec.threshold])
                for row in rows:
                    value, key, matched, score = row[0], row[1], row[2], float(row[3])
                    self._keep(best, str(value), Match(str(value), str(matched), score,
                                                       None if key is None else str(key)))
            else:
                sql = within_file_sql(self.spec, len(batch))
                rows = self.execute(sql, [*batch, self.spec.threshold])
                for row in rows:
                    left, right, score = str(row[0]), str(row[1]), float(row[2])
                    # Both sides are duplicates of each other, so both are reported.
                    # Flagging only the second would make the verdict depend on row
                    # order, which is not a property of the data.
                    self._keep(best, left, Match(left, right, score))
                    self._keep(best, right, Match(right, left, score))
        return best

    def clusters(self, values: Sequence[Any]) -> Dict[str, str]:
        """value -> the canonical name of its near-duplicate cluster.

        This is what lets `semantic` be one component of an AND. An exact
        component keys on the raw value and a fuzzy one on its blocking key; a
        semantic component keys on WHICH CLUSTER the value fell into, and then the
        composite comparison is the same tuple equality as before.

        Clusters are the connected components of the "scored above threshold"
        graph. Cosine similarity is NOT transitive - a~b and b~c does not give
        a~c - so joining them can merge two things that were never compared
        favourably. That is deliberate: it errs toward reporting a duplicate that
        a human then dismisses, and for a duplication check over-reporting is the
        recoverable direction. Under-reporting looks like a clean file.

        A value with no match maps to itself, so it can only ever tie with an
        identical value - which is what an exact match would have said anyway.
        """
        parent: Dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                # Smallest member wins, so the canonical name does not depend on
                # the order rows happened to arrive in.
                lo, hi = (ra, rb) if ra <= rb else (rb, ra)
                parent[hi] = lo

        for value, match in self.find(values).items():
            union(value, match.matched)

        out: Dict[str, str] = {}
        for value in candidate_values(values, self.spec.min_length):
            out[value] = find(value) if value in parent else value
        return out

    @staticmethod
    def _keep(best: Dict[str, Match], key: str, match: Match) -> None:
        current = best.get(key)
        if current is None or match.score > current.score:
            best[key] = match
