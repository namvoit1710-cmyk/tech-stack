"""A gateway name must survive the URL path it is addressed through.

Measured on tenant-1 2026-08-12: the registry held **61 apps and exactly one
was real**. The other 60 were OWASP ZAP scan payloads written through
``POST /api/v1/gateway/apps`` in three waves (the last one 2026-08-11), and
**42 of them could not be deleted at all** — their names contain ``/``, ``%2F``
is percent-decoded before routing, and ``{name}`` matches a single path
segment, so ``GET``/``PATCH``/``DELETE`` every one returned 404. They had to be
removed straight out of HANA.

The tell is in the surviving data: the 19 rows an earlier cleanup *did* manage
to retire are exactly the addressable names, and the 42 it left are exactly the
ones containing a slash. ``6801356905166257676.owasp.org`` is gone;
``https://6801356905166257676.owasp.org`` is not.

The rule that closes it lives in the domain (``resource_name.name_error``) and
is enforced on both entities, on create AND on rename, because renaming into a
bad name strands a row just as thoroughly as creating one.

Two things these tests deliberately pin as *not* happening:

* hydration is never validated — a pre-existing bad row must stay readable and
  deletable, or the rule rebuilds the trap it closes;
* ``openapi_import._slug`` cannot emit a rejectable name, so tightening the
  registry cannot break an OpenAPI sync.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.gateway_app import GatewayApp
from app.layer1_domain.entities.gateway_function import GatewayFunction
from app.layer1_domain.exceptions import ValidationError
from app.layer1_domain.value_objects.auth_type import AuthType
from app.layer1_domain.value_objects.gateway import (
    AppKind,
    FunctionParam,
    HttpMethod,
    MAX_NAME_LENGTH,
    PathTemplate,
    name_error,
    parse_openapi,
)
from app.layer1_domain.value_objects.gateway.openapi_import import _slug

# Verbatim from AIW_GATEWAY_APPS on tenant-1, 2026-08-12, before the purge.
# Every one of these was written by a scanner and none could be deleted.
UNDELETABLE_ON_TENANT_1 = [
    "/etc/passwd",
    "c:/Windows/system.ini",
    "../../../../../../../../../../../../../../../../etc/passwd",
    "/",
    "WEB-INF/web.xml",
    "/WEB-INF/web.xml",
    "http://www.google.com/search?q=ZAP",
    "www.google.com:80/search?q=ZAP",
    "https://7405632686557278988%2eowasp%2eorg",
    "URL='http://7405632686557278988.owasp.org'",
    "5;URL='https://7405632686557278988.owasp.org/?ZAP'",
    "https://\\7405632686557278988.owasp.org",
    '<!--#EXEC cmd="ls /"-->',
    '"><!--#EXEC cmd="ls /"--><',
    "() { :;}; /bin/sleep 15",
    "c:\\Windows\\system.ini",
    "\\WEB-INF\\web.xml",
]

# Names that must keep working. ``workflow-api`` is the one real app on
# tenant-1; the rest are shapes a human would reasonably type.
LEGITIMATE = [
    "workflow-api",
    "sap-s4hana",
    "billing_v2",
    "orders.v1",
    "a",
    "A1",
    "x" * MAX_NAME_LENGTH,
]


def _app(name: str) -> GatewayApp:
    return GatewayApp.create(
        name=name, kind=AppKind.INTERNAL, base_url="https://example.com",
        auth_type=AuthType.NONE, created_by="tester",
    )


def _function(name: str) -> GatewayFunction:
    return GatewayFunction.create(
        app_id="app-1", name=name, method=HttpMethod.GET,
        path_template=PathTemplate("/things"), created_by="tester",
    )


class TestTheRuleItself:
    @pytest.mark.parametrize("name", LEGITIMATE)
    def test_legitimate_names_pass(self, name):
        assert name_error(name) is None

    @pytest.mark.parametrize("name", UNDELETABLE_ON_TENANT_1)
    def test_every_name_that_was_stuck_on_tenant_1_is_refused(self, name):
        assert name_error(name) is not None

    def test_the_length_bound_matches_the_column(self):
        """NVARCHAR(128) on both tables. One over used to pass every check and
        then fail at INSERT as a 500."""
        assert name_error("x" * MAX_NAME_LENGTH) is None
        assert name_error("x" * (MAX_NAME_LENGTH + 1)) is not None

    @pytest.mark.parametrize("name", ["", "   ", None])
    def test_empty_is_still_refused(self, name):
        assert name_error(name) is not None

    @pytest.mark.parametrize("name", [".", "..", ".hidden", "-lead", "_lead"])
    def test_a_name_cannot_start_with_a_path_token(self, name):
        """``.`` and ``..`` are relative-path tokens wherever the name is
        interpolated; requiring an alphanumeric first character rules out the
        whole family rather than blacklisting two strings."""
        assert name_error(name) is not None

    def test_the_message_says_why_not_just_no(self):
        msg = name_error("WEB-INF/web.xml")
        assert "addressable" in msg and "WEB-INF/web.xml" in msg


class TestBothEntitiesEnforceIt:
    @pytest.mark.parametrize("name", ["WEB-INF/web.xml", "x" * 200])
    def test_app_create_refuses(self, name):
        with pytest.raises(ValidationError):
            _app(name)

    @pytest.mark.parametrize("name", ["WEB-INF/web.xml", "x" * 200])
    def test_function_create_refuses(self, name):
        with pytest.raises(ValidationError):
            _function(name)

    def test_app_rename_refuses(self):
        """A good app renamed into a bad name is stranded exactly the same
        way — the rename path is the one an audit of ``create`` alone misses."""
        app = _app("workflow-api")
        with pytest.raises(ValidationError):
            app.rename("c:/Windows/system.ini", updated_by="tester")
        assert app.name == "workflow-api", "must not be left half-applied"

    def test_function_rename_refuses(self):
        fn = _function("get_orders")
        with pytest.raises(ValidationError):
            fn.rename("../../etc/passwd", updated_by="tester")
        assert fn.name == "get_orders"

    def test_a_good_rename_still_works(self):
        app = _app("workflow-api")
        app.rename("workflow-api-v2", updated_by="tester")
        assert app.name == "workflow-api-v2" and app.version == 2


class TestHydrationIsNotValidated:
    """The rule must not make an existing bad row unreadable.

    If ``__post_init__`` or the repo's row mapping validated, the 42 stuck rows
    would have become unreadable as well as undeletable — the same trap, one
    layer deeper. Construction-from-storage stays permissive; only ``create``
    and the mutators enforce.
    """

    def test_a_bad_row_still_hydrates(self):
        app = GatewayApp(
            id="ghost-1", name="WEB-INF/web.xml", kind=AppKind.INTERNAL,
            base_url="John Doe", created_by="system", updated_by="system",
        )
        assert app.name == "WEB-INF/web.xml"

    def test_and_can_still_be_deactivated(self):
        """``deactivate`` is what the delete use case calls. It must work on a
        row the rule would refuse to create."""
        app = GatewayApp(
            id="ghost-1", name="WEB-INF/web.xml", kind=AppKind.INTERNAL,
            base_url="John Doe", created_by="system", updated_by="system",
        )
        app.deactivate(updated_by="janitor")
        assert app.is_active is False

    def test_but_validate_reports_it(self):
        """Readable is not the same as valid — an audit sweep must still see it."""
        app = GatewayApp(
            id="ghost-1", name="WEB-INF/web.xml", kind=AppKind.INTERNAL,
            base_url="John Doe", created_by="system", updated_by="system",
        )
        assert any("addressable" in e for e in app.validate())


class TestOpenapiSyncCannotBeBrokenByTheRule:
    """``_slug`` folds everything non-alphanumeric to ``_``, so a synced name
    is inside the character set by construction. This is what makes tightening
    the registry safe for the producer path."""

    @pytest.mark.parametrize("op_id", [
        "getOrder", "get /orders/{id}", "Ops::List Things", "../../etc/passwd",
        "<!--#EXEC-->", "http://www.google.com/search?q=ZAP", "", "___",
        "x" * 400,
    ])
    def test_slug_output_is_always_acceptable(self, op_id):
        assert name_error(_slug(op_id)) is None

    def test_a_long_operation_id_is_capped_with_room_for_the_dedupe_suffix(self):
        """The de-dupe loop appends ``_2``/``_37`` AFTER slugging, so the cap
        has to leave headroom or the collision case reintroduces the overflow."""
        slug = _slug("op_" + "x" * 400)
        assert len(slug) <= MAX_NAME_LENGTH - 8
        assert name_error(f"{slug}_9999") is None

    def test_a_real_spec_with_hostile_operation_ids_parses_to_usable_names(self):
        spec = {
            "paths": {
                "/orders/{id}": {
                    "get": {"operationId": "../../../../etc/passwd"},
                    "post": {"operationId": "<!--#EXEC cmd=\"ls /\"-->"},
                    "put": {},  # no operationId -> method_path fallback
                },
            },
        }
        sigs = parse_openapi(spec, kind=AppKind.INTERNAL)
        assert len(sigs) == 3
        for sig in sigs:
            assert name_error(sig.name) is None, sig.name

    def test_dedupe_still_produces_distinct_acceptable_names(self):
        """Two operationIds that slug to the same string must not collide into
        one function — and the disambiguated name must still be acceptable."""
        spec = {
            "paths": {
                "/a": {"get": {"operationId": "list/things"}},
                "/b": {"get": {"operationId": "list.things"}},
            },
        }
        names = [s.name for s in parse_openapi(spec, kind=AppKind.INTERNAL)]
        assert len(set(names)) == 2, names
        assert all(name_error(n) is None for n in names)


class TestTheParamsAreNotAffected:
    """Only the two NAME columns are route keys. Param keys are body/query
    material and keep their own (looser) rules — tightening them here would be
    scope creep with a migration attached."""

    def test_a_param_key_with_a_dot_is_untouched(self):
        fn = GatewayFunction.create(
            app_id="app-1", name="get_thing", method=HttpMethod.GET,
            path_template=PathTemplate("/things"),
            input_params=[FunctionParam(key="filter.name", location="query")],
            created_by="tester",
        )
        assert fn.input_params[0].key == "filter.name"
