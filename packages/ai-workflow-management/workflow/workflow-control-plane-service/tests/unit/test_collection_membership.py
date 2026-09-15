"""SA-2273 semantics A — "the collection contains x" answered at the file service.

    {{$collection}} CONTAINS "x"   ==   some row has some column equal to x

answered with ``$filter`` + ``$count=true`` + ``$top=0``: one call, a number
back, zero rows crossing into the control plane.

The bug being fixed: the pointer used to be interpolated into the expression
string before the operator ran, so the comparison happened against the JSON of
the file REFERENCE ENVELOPE. ``CONTAINS "A1"`` answered False where the data
plainly contains A1, and ``CONTAINS "csv"`` answered True by matching
``content_type``.
"""

from __future__ import annotations

import re

import pytest

from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.resolve import odata_predicate as op
from app.layer2_application.resolve.collection_membership import (
    UNKNOWN,
    CollectionMembershipProbe,
)
from app.layer2_application.resolve.file_backed_resolver import (
    FileBackedResolverStrategy,
)
from app.layer2_application.resolve.odata_predicate import (
    any_column_equals,
    eligible_columns,
)
from app.layer2_application.resolve.variable_resolver import (
    ConditionEvaluator,
    TraversalResolverStrategy,
    VariableResolver,
    reset_current_run_id,
    set_current_run_id,
)
from app.layer4_frameworks.clients.mock_file_service_client import (
    MockFileServiceClient,
)


# ---------------------------------------------------------------------------
# A fake that speaks the REAL file service's $filter grammar.
#
# MockFileServiceClient parses one predicate only and silently returns EVERY
# row for anything it cannot parse — which reads as "the collection contains
# everything" and hid a real defect during development. This fake implements
# the grammar in
# apps/backend/file/app/layer3_adapters/odata/odata_parser.py instead.
# ---------------------------------------------------------------------------

_PREDICATE = re.compile(r"^([A-Za-z_][\w.]*)\s+eq\s+'(.*)'$")


def _split_or(expr: str) -> list[str]:
    """Reimplementation of ``_split_by_operator(expr, ' or ')`` — depth and
    quote aware, which is exactly why outer parentheses break the real parser."""
    parts, current, depth, in_q, qc, i = [], "", 0, False, "", 0
    while i < len(expr):
        ch = expr[i]
        if ch in "\"'" and (i == 0 or expr[i - 1] != "\\"):
            if not in_q:
                in_q, qc = True, ch
            elif ch == qc:
                in_q, qc = False, ""
            current += ch
            i += 1
            continue
        if not in_q and ch == "(":
            depth += 1
        elif not in_q and ch == ")":
            depth -= 1
        if not in_q and depth == 0 and expr[i:i + 4].lower() == " or ":
            parts.append(current)
            current = ""
            i += 4
            continue
        current += ch
        i += 1
    parts.append(current)
    return [p for p in parts if p.strip()]


class _GrammarFaithfulClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[dict] = []
        self.rows_returned = 0

    async def query_file_data(self, file_id: str, params: dict) -> dict:
        self.calls.append(dict(params))
        expr = params.get("$filter", "")
        clauses = []
        for part in _split_or(expr):
            m = _PREDICATE.match(part.strip())
            if not m:
                # The real parser raises; the service turns that into a 4xx.
                raise ValueError(
                    f"Unable to determine field name in $filter segment: {part}")
            clauses.append((m.group(1), m.group(2)))
        matched = [r for r in self.rows
                   if any(str(r.get(f, "")) == v for f, v in clauses)]
        top = int(params.get("$top", "100"))
        data = matched[:top]
        self.rows_returned += len(data)
        return {"file_id": file_id, "total_matches": len(matched), "data": data}


def _ref(**over):
    base = {
        "__file_ref": True,
        "file_id": "f-1",
        "content_type": "text/csv",
        "columns": ["sku", "qty", "status"],
        "column_types": {"sku": "string", "qty": "number", "status": "string"},
        "row_count": 3,
    }
    base.update(over)
    return base


ROWS = [
    {"sku": "A1", "qty": "3", "status": "open"},
    {"sku": "B2", "qty": "9", "status": "done"},
    {"sku": "C3", "qty": "17", "status": "open"},
]


# ---------------------------------------------------------------------------
# The grammar contract with the real parser
# ---------------------------------------------------------------------------


class TestFilterGrammarMatchesTheRealParser:
    """Two failure modes, both silent, both found by reading the real parser."""

    def test_no_outer_parentheses(self):
        f = any_column_equals(["sku", "status"], {"sku": "string", "status": "string"}, "A1")
        assert f is not None
        assert not f.startswith("("), (
            "an outer paren keeps ' or ' at depth 1, so the real splitter never "
            "splits and the whole string is parsed as ONE predicate — which fails"
        )

    def test_every_or_part_starts_with_a_bare_field(self):
        f = any_column_equals(["sku", "status"], {"sku": "string", "status": "string"}, "A1")
        for part in _split_or(f):
            assert _PREDICATE.match(part.strip()), (
                f"{part!r} does not match ^<field> eq '<value>'$, which is what "
                "_extract_field_name anchors on"
            )

    def test_a_value_containing_or_does_not_split(self):
        f = any_column_equals(["sku"], {"sku": "string"}, "cat or dog")
        assert len(_split_or(f)) == 1, "a literal containing ' or ' split the filter"

    def test_in_translator_is_also_unwrapped(self):
        """Same defect, same fix — it was wrapped when first written."""
        rendered = op.translate("sku", "IN", ["A1", "B2"])
        assert not rendered.startswith("(")
        assert len(_split_or(rendered)) == 2


# ---------------------------------------------------------------------------
# Type narrowing
# ---------------------------------------------------------------------------


class TestTypeNarrowing:
    def test_a_string_literal_only_compares_string_columns(self):
        cols = eligible_columns(
            ["sku", "qty"], {"sku": "string", "qty": "number"}, "A1")
        assert cols == ["sku"], (
            "qty eq 'A1' against a numeric column is a type error, and a "
            "backend may reject the whole filter for it"
        )

    def test_a_number_literal_only_compares_number_columns(self):
        cols = eligible_columns(
            ["sku", "qty"], {"sku": "string", "qty": "number"}, 9)
        assert cols == ["qty"]

    def test_json_columns_accept_a_string(self):
        cols = eligible_columns(["blob"], {"blob": "json"}, "x")
        assert cols == ["blob"]

    def test_synthetic_columns_are_never_matched(self):
        cols = eligible_columns(
            ["__row_id", "__parent_row_id", "sku"],
            {"__row_id": "number", "sku": "string"}, "A1")
        assert cols == ["sku"], (
            "matching storage bookkeeping is the envelope-vocabulary bug again"
        )

    def test_no_eligible_column_is_untranslatable(self):
        assert any_column_equals(["qty"], {"qty": "number"}, "text") is None


class TestFilterSizeCaps:
    def test_too_many_columns_falls_back(self):
        cols = [f"c{i}" for i in range(op.MAX_FILTER_COLUMNS + 1)]
        types = {c: "string" for c in cols}
        assert any_column_equals(cols, types, "x") is None

    def test_at_the_cap_it_still_builds(self):
        cols = [f"c{i}" for i in range(op.MAX_FILTER_COLUMNS)]
        types = {c: "string" for c in cols}
        assert any_column_equals(cols, types, "x") is not None

    def test_encoded_length_budget_is_enforced(self):
        """Within the COLUMN cap but over the byte budget — long names alone
        can push a filter past a gateway's request-line limit."""
        cols = [f"{'c' * 120}{i:02d}" for i in range(op.MAX_FILTER_COLUMNS)]
        types = {c: "string" for c in cols}
        naive = " or ".join(f"{c} eq 'x'" for c in cols)
        assert len(naive) > op.MAX_FILTER_ENCODED_BYTES, "test data too small"
        assert any_column_equals(cols, types, "x") is None


# ---------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------


class TestProbeAnswers:
    async def test_present_value_is_true(self):
        client = _GrammarFaithfulClient(ROWS)
        assert await CollectionMembershipProbe(client).contains(_ref(), "A1") is True

    async def test_absent_value_is_false(self):
        client = _GrammarFaithfulClient(ROWS)
        assert await CollectionMembershipProbe(client).contains(_ref(), "ZZZZ") is False

    async def test_zero_rows_cross_into_the_control_plane(self):
        client = _GrammarFaithfulClient(ROWS)
        await CollectionMembershipProbe(client).contains(_ref(), "A1")
        assert client.calls[0]["$top"] == "0"
        assert client.calls[0]["$count"] == "true"
        assert client.rows_returned == 0, (
            "rows came back for a membership question — the transfer this "
            "exists to avoid"
        )

    async def test_one_call_only(self):
        client = _GrammarFaithfulClient(ROWS)
        await CollectionMembershipProbe(client).contains(_ref(), "A1")
        assert len(client.calls) == 1

    async def test_a_column_name_is_not_data(self):
        """'sku' is a COLUMN, not a value — it must not satisfy membership."""
        client = _GrammarFaithfulClient(ROWS)
        assert await CollectionMembershipProbe(client).contains(_ref(), "sku") is False

    async def test_an_empty_collection_answers_false_without_calling(self):
        client = _GrammarFaithfulClient(ROWS)
        got = await CollectionMembershipProbe(client).contains(
            _ref(file_id="", row_count=0), "A1")
        assert got is False
        assert client.calls == []


class TestProbeRefusals:
    """Every refusal is UNKNOWN — never a guess."""

    async def test_excel_is_refused(self):
        client = _GrammarFaithfulClient(ROWS)
        got = await CollectionMembershipProbe(client).contains(
            _ref(content_type="application/vnd.ms-excel"), "A1")
        assert got is UNKNOWN
        assert client.calls == [], (
            "pl.read_excel is EAGER — filtering an xlsx moves the OOM into the "
            "file service instead of removing it"
        )

    async def test_no_eligible_column_is_unknown(self):
        client = _GrammarFaithfulClient(ROWS)
        got = await CollectionMembershipProbe(client).contains(
            _ref(columns=["qty"], column_types={"qty": "number"}), "text")
        assert got is UNKNOWN

    async def test_a_service_error_is_unknown_not_false(self):
        class _Boom:
            async def query_file_data(self, *_a, **_k):
                raise RuntimeError("503")

        assert await CollectionMembershipProbe(_Boom()).contains(_ref(), "A1") is UNKNOWN

    async def test_a_response_without_a_count_is_unknown_not_false(self):
        class _Empty:
            async def query_file_data(self, *_a, **_k):
                return {"file_id": "f-1"}

        got = await CollectionMembershipProbe(_Empty()).contains(_ref(), "A1")
        assert got is UNKNOWN, "a missing count was read as zero — a wrong answer"

    async def test_no_client_is_unknown(self):
        assert await CollectionMembershipProbe(None).contains(_ref(), "A1") is UNKNOWN

    async def test_a_non_ref_is_unknown(self):
        client = _GrammarFaithfulClient(ROWS)
        assert await CollectionMembershipProbe(client).contains({"a": 1}, "x") is UNKNOWN


class TestProbeMetrics:
    async def test_answers_and_fallbacks_are_counted(self):
        class _Mon:
            def __init__(self):
                self.seen = []

            def increment(self, metric, tags=None):
                self.seen.append((metric, dict(tags or {})))

        mon = _Mon()
        probe = CollectionMembershipProbe(_GrammarFaithfulClient(ROWS), monitor=mon)
        await probe.contains(_ref(), "A1")
        await probe.contains(_ref(content_type="application/vnd.ms-excel"), "A1")
        metrics = [m for m, _ in mon.seen]
        assert "collection_membership_probe_total" in metrics
        assert "collection_membership_fallback_total" in metrics
        causes = [t.get("cause") for m, t in mon.seen if "fallback" in m]
        assert "not_csv_backed" in causes


# ---------------------------------------------------------------------------
# End to end: inline and file-backed must AGREE
# ---------------------------------------------------------------------------


@pytest.fixture
def run_id():
    token = set_current_run_id("membership-run")
    try:
        yield
    finally:
        reset_current_run_id(token)


class TestInlineAndFileBackedAgree:
    """The parity axis SA-2264 did not cover, and the one this bug lives on."""

    async def _evaluators(self):
        client = MockFileServiceClient()
        store = DataStoreService(client, CsvConverter())
        data = [{"sku": "A1", "qty": 3}, {"sku": "B2", "qty": 9}]
        ref = await store.store_data("orders", data)
        rows = [{"sku": "A1", "qty": "3"}, {"sku": "B2", "qty": "9"}]
        probe_client = _GrammarFaithfulClient(rows)
        file_ev = ConditionEvaluator(
            VariableResolver([FileBackedResolverStrategy(store, file_client=client)]),
            membership_probe=CollectionMembershipProbe(probe_client),
        )
        inline_ev = ConditionEvaluator(VariableResolver([TraversalResolverStrategy()]))
        return inline_ev, file_ev, data, ref

    @pytest.mark.parametrize("needle,expected", [
        ("A1", True),
        ("B2", True),
        ("ZZZZ", False),
    ])
    async def test_real_values_agree(self, run_id, needle, expected):
        inline_ev, file_ev, data, ref = await self._evaluators()
        expr = '{{$orders}} CONTAINS "%s"' % needle
        got_file = await file_ev.evaluate(expr, {"orders": ref})
        got_inline = await inline_ev.evaluate(expr, {"orders": data})
        assert got_file is expected
        assert got_inline is expected
        assert got_file == got_inline

    @pytest.mark.parametrize("envelope_word", ["csv", "__file_ref", "text/csv"])
    async def test_envelope_vocabulary_never_matches(self, run_id, envelope_word):
        """The false-positive half: these words live in the REFERENCE, and
        matching them was reporting internal plumbing as customer data."""
        _inline, file_ev, _data, ref = await self._evaluators()
        expr = '{{$orders}} CONTAINS "%s"' % envelope_word
        assert await file_ev.evaluate(expr, {"orders": ref}) is False

    async def test_in_operator_mirrors_contains(self, run_id):
        _inline, file_ev, _data, ref = await self._evaluators()
        assert await file_ev.evaluate('"A1" IN {{$orders}}', {"orders": ref}) is True
        assert await file_ev.evaluate('"ZZ" IN {{$orders}}', {"orders": ref}) is False


class TestEverythingElseIsUntouched:
    async def test_without_a_probe_behaviour_is_unchanged(self, run_id):
        client = MockFileServiceClient()
        store = DataStoreService(client, CsvConverter())
        ref = await store.store_data("orders", [{"sku": "A1"}])
        ev = ConditionEvaluator(
            VariableResolver([FileBackedResolverStrategy(store, file_client=client)]))
        # No probe wired: the old path runs, whatever it answers.
        assert isinstance(
            await ev.evaluate('{{$orders}} CONTAINS "A1"', {"orders": ref}), bool)

    async def test_non_membership_operators_are_not_intercepted(self, run_id):
        client = MockFileServiceClient()
        store = DataStoreService(client, CsvConverter())
        ref = await store.store_data("orders", [{"sku": "A1"}])
        probe_client = _GrammarFaithfulClient([{"sku": "A1"}])
        ev = ConditionEvaluator(
            VariableResolver([FileBackedResolverStrategy(store, file_client=client)]),
            membership_probe=CollectionMembershipProbe(probe_client))
        await ev.evaluate('{{$orders}} == "A1"', {"orders": ref})
        assert probe_client.calls == [], (
            "'==' over a collection was pushed down; only membership operators "
            "should be"
        )

    async def test_a_scalar_operand_is_not_intercepted(self, run_id):
        client = MockFileServiceClient()
        store = DataStoreService(client, CsvConverter())
        ref = await store.store_data("orders", [{"sku": "A1"}])
        probe_client = _GrammarFaithfulClient([{"sku": "A1"}])
        ev = ConditionEvaluator(
            VariableResolver([FileBackedResolverStrategy(store, file_client=client)]),
            membership_probe=CollectionMembershipProbe(probe_client))
        await ev.evaluate('{{$orders[0].sku}} CONTAINS "A"', {"orders": ref})
        assert probe_client.calls == [], (
            "an indexed path resolves to a scalar, not a collection"
        )
