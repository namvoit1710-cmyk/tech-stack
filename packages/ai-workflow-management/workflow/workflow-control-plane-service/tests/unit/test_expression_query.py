"""Tests for the pure parse/match/rank helpers behind design-time autocomplete."""

from dataclasses import dataclass, field

from app.layer2_application.features.get_design_time_variable_suggestions.use_cases.expression_query import (
    ParsedQuery,
    PathMatch,
    match,
    parse_query,
    rank_key,
    source_token,
)


# ---------------------------------------------------------------------------
# Lightweight stand-ins (shape-compatible with Scope / VariablePath)
# ---------------------------------------------------------------------------

@dataclass
class _Path:
    path: str = ""
    kind: str = "scalar"
    output_type: str = "string"
    item_type: str | None = None
    label: str = ""
    display_path: str = ""


@dataclass
class _Scope:
    scope_type: str = "node"
    scope_id: str = ""
    label: str = ""
    expression_prefix: str = ""
    node_type: str | None = None
    paths: list = field(default_factory=list)


def _node_scope(node_id: str, paths: list[str]) -> _Scope:
    return _Scope(
        scope_type="node", scope_id=node_id, label=node_id,
        expression_prefix=f"{{{{${node_id}",
        paths=[_Path(path=p) for p in paths],
    )


def _system_scope(scope_type: str, paths: list[str]) -> _Scope:
    return _Scope(
        scope_type=scope_type, scope_id=scope_type, label=scope_type,
        expression_prefix=f"{{{{${scope_type}",
        paths=[_Path(path=p) for p in paths],
    )


# ---------------------------------------------------------------------------
# parse_query
# ---------------------------------------------------------------------------

class TestParseQuery:
    def test_empty(self):
        p = parse_query("")
        assert p.is_empty is True

    def test_whitespace_only(self):
        assert parse_query("   ").is_empty is True

    def test_braces_and_dollar_only(self):
        p = parse_query("{{$")
        assert p.is_empty is True
        assert p.explicit_source is True

    def test_full_prefix_form(self):
        p = parse_query("{{$nA.da")
        assert p == ParsedQuery(
            source_term="nA", path_term="da",
            has_separator=True, explicit_source=True,
        )

    def test_dollar_form(self):
        p = parse_query("$nA.da")
        assert p.source_term == "nA" and p.path_term == "da"
        assert p.explicit_source is True

    def test_bare_form_with_dot(self):
        p = parse_query("nA.da")
        assert p.source_term == "nA" and p.has_separator is True
        assert p.explicit_source is False

    def test_bare_term(self):
        p = parse_query("da")
        assert p.source_term == "da" and p.has_separator is False
        assert p.explicit_source is False

    def test_trailing_dot(self):
        p = parse_query("$nA.")
        assert p.source_term == "nA"
        assert p.path_term == ""
        assert p.has_separator is True

    def test_trailing_closing_braces_stripped(self):
        p = parse_query("{{$nA.da}}")
        assert p.source_term == "nA" and p.path_term == "da"

    def test_single_trailing_brace_stripped(self):
        assert parse_query("{{$nA.da}").path_term == "da"

    def test_variable_alias_canonicalized(self):
        # Picker exposes only $variables; the singular $variable is aliased to it.
        assert parse_query("$variable.x").source_term == "variables"
        assert parse_query("$Variable.x").source_term == "variables"

    def test_trailing_open_bracket_dropped(self):
        p = parse_query("$nA.orders[")
        assert p.path_term == "orders"

    def test_trailing_partial_index_dropped(self):
        assert parse_query("$nA.orders[0").path_term == "orders"
        assert parse_query("$nA.orders[*").path_term == "orders"

    def test_complete_numeric_index_canonicalized_to_star(self):
        p = parse_query("$nA.orders[0].sku")
        assert p.path_term == "orders[*].sku"

    def test_only_path_after_multiple_dots(self):
        p = parse_query("$nA.address.city")
        assert p.source_term == "nA" and p.path_term == "address.city"


# ---------------------------------------------------------------------------
# source_token
# ---------------------------------------------------------------------------

class TestSourceToken:
    def test_node_scope_uses_scope_id(self):
        assert source_token(_node_scope("nA", [])) == "nA"

    def test_system_scope_uses_scope_type(self):
        assert source_token(_system_scope("variables", [])) == "variables"


# ---------------------------------------------------------------------------
# match — semantics per query shape
# ---------------------------------------------------------------------------

class TestMatch:
    def _scopes(self):
        return [
            _system_scope("run", ["id", "status"]),
            _system_scope("input", ["customer_id", "data.name"]),
            _node_scope("nA", ["address", "address.city", "velocity"]),
        ]

    def test_empty_query_returns_everything_in_provider_order(self):
        out = match(parse_query(""), self._scopes())
        assert [m.match_kind for m in out] == ["all"] * 7
        assert [m.path.path for m in out] == [
            "id", "status", "customer_id", "data.name",
            "address", "address.city", "velocity",
        ]

    def test_source_prefix_suggests_sources(self):
        out = match(parse_query("$in"), self._scopes())
        assert len(out) == 1
        assert out[0].match_kind == "source"
        assert out[0].scope.scope_type == "input"

    def test_exact_source_without_dot_also_emits_paths(self):
        out = match(parse_query("$input"), self._scopes())
        kinds = [m.match_kind for m in out]
        assert kinds[0] == "source"
        assert kinds[1:] == ["all", "all"]

    def test_source_plus_trailing_dot_emits_all_paths(self):
        out = match(parse_query("$nA."), self._scopes())
        assert [m.path.path for m in out] == ["address", "address.city", "velocity"]
        assert all(m.match_kind == "all" for m in out)

    def test_path_prefix_filtering(self):
        out = match(parse_query("$nA.add"), self._scopes())
        assert [m.path.path for m in out] == ["address", "address.city"]
        assert all(m.match_kind == "prefix" for m in out)

    def test_prefix_ranked_before_substring(self):
        out = match(parse_query("$nA.city"), self._scopes())
        # both "address.city" and "velo-city" match only as substrings
        paths = [(m.path.path, m.match_kind) for m in out]
        assert ("address.city", "substring") in paths
        assert ("velocity", "substring") in paths

    def test_case_insensitive_matching(self):
        out = match(parse_query("$NA.ADD"), self._scopes())
        assert [m.path.path for m in out] == ["address", "address.city"]

    def test_unknown_source_yields_empty(self):
        assert match(parse_query("$ghost.x"), self._scopes()) == []

    def test_bare_term_searches_sources_and_all_scopes(self):
        out = match(parse_query("add"), self._scopes())
        assert all(m.match_kind in ("source", "prefix", "substring") for m in out)
        assert {m.path.path for m in out if m.path} == {"address", "address.city"}

    def test_bare_term_matching_source_token(self):
        out = match(parse_query("in"), self._scopes())
        source_hits = [m for m in out if m.match_kind == "source"]
        assert len(source_hits) == 1
        assert source_hits[0].scope.scope_type == "input"


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

class TestRanking:
    def test_prefix_beats_substring(self):
        scope = _node_scope("n", ["zzz_name", "name"])
        out = match(parse_query("$n.name"), [scope])
        assert [m.path.path for m in out] == ["name", "zzz_name"]
        assert [m.match_kind for m in out] == ["prefix", "substring"]

    def test_shallower_path_first(self):
        scope = _node_scope("n", ["address.city.zip", "address"])
        out = match(parse_query("$n.addr"), [scope])
        assert [m.path.path for m in out] == ["address", "address.city.zip"]

    def test_shorter_path_first_at_same_depth(self):
        scope = _node_scope("n", ["ab_longer", "ab"])
        out = match(parse_query("$n.ab"), [scope])
        assert [m.path.path for m in out] == ["ab", "ab_longer"]

    def test_stable_on_ties(self):
        scope = _node_scope("n", ["aa", "ab"])
        out = match(parse_query("$n.a"), [scope])
        assert [m.path.path for m in out] == ["aa", "ab"]

    def test_rank_key_orders_source_path_empty_first(self):
        m_source = PathMatch(scope=_node_scope("n", []), path=None, match_kind="source")
        key = rank_key(m_source, 0)
        assert key[1] == 0 and key[2] == 0
