"""Unit specs for layer2 application node module `plan_execute_nodes`.

Target: app/layer2_application/features/execute_agent/nodes/plan_execute_nodes.py
Units:
  - build_material_step_records  (async; collaborators: open_ai_service [async
    get_chat_completion -> AsyncMock], fetch_section_definitions [async, patched],
    app_settings_adapter [sync attr access -> MagicMock])
  - parse_llm_response           (pure)
  - map_to_step_records          (pure, delegates to StepRecord.from_dict)
  - enum_values                  (pure)

ENV NOTE (env-blocker, honest): plan_execute_nodes.py uses a backslash inside an
f-string expression at module scope (`{"\\n".join(...)}`), which is only legal on
Python >= 3.12 (PEP 701). The only interpreter available for this sweep is
Python 3.11.0, under which importing this module raises SyntaxError, so these
specs CANNOT be collected/executed here. They are authored source-grounded and
marked VERIFIED-STATIC. A module-level skip guard keeps the rest of the suite
green; on a 3.12+ env these run as ordinary tests. See the sweep evidence note.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# --- env guard: the target module needs Python >= 3.12 to even import ---------
_MOD = "app.layer2_application.features.execute_agent.nodes.plan_execute_nodes"
try:  # pragma: no cover - import guard
    import importlib

    _pen = importlib.import_module(_MOD)
    parse_llm_response = _pen.parse_llm_response
    map_to_step_records = _pen.map_to_step_records
    enum_values = _pen.enum_values
    build_material_step_records = _pen.build_material_step_records
    _IMPORT_ERR = None
except Exception as exc:  # SyntaxError under < 3.12, or missing SDK deps
    _IMPORT_ERR = exc

pytestmark = pytest.mark.skipif(
    _IMPORT_ERR is not None,
    reason=f"plan_execute_nodes not importable in this env (>=3.12 required): {_IMPORT_ERR!r}",
)

from app.layer1_domain.entities.enums import AssignType  # noqa: E402
from app.layer1_domain.entities.section_definition import SectionDefinition  # noqa: E402


# ---------------------------------------------------------------------------
# enum_values
# ---------------------------------------------------------------------------
class TestEnumValues:
    def test_happy_joins_enum_values(self):
        # grounded: ", ".join([e.value for e in enum_cls])
        assert enum_values(AssignType) == "USER, GROUP, SYSTEM, ROLE"


# ---------------------------------------------------------------------------
# parse_llm_response
# ---------------------------------------------------------------------------
class TestParseLlmResponse:
    def test_happy_returns_records_list(self):
        assert parse_llm_response('{"records": [{"stepId": 1}]}') == [{"stepId": 1}]

    def test_invalid_empty_string_raises(self):
        with pytest.raises(ValueError, match="Empty response from LLM"):
            parse_llm_response("")

    def test_invalid_bad_json_raises(self):
        with pytest.raises(ValueError, match="Invalid JSON from LLM"):
            parse_llm_response("{not json")

    def test_edge_non_dict_top_level_raises(self):
        with pytest.raises(ValueError, match="must be a JSON object"):
            parse_llm_response("[1, 2, 3]")

    def test_boundary_missing_records_key_raises(self):
        with pytest.raises(ValueError, match="Missing required key"):
            parse_llm_response('{"other": 1}')

    def test_failure_records_not_a_list_raises(self):
        with pytest.raises(ValueError, match="'records' must be a list"):
            parse_llm_response('{"records": {"x": 1}}')

    def test_boundary_empty_records_list_ok(self):
        assert parse_llm_response('{"records": []}') == []


# ---------------------------------------------------------------------------
# map_to_step_records
# ---------------------------------------------------------------------------
class TestMapToStepRecords:
    def _sections(self):
        return [SectionDefinition(section="Finance", fields=["vc"])]

    def _record(self, **over):
        base = {
            "description": "approve Finance",
            "stepId": 1,
            "stepOrder": 1,
            "message": "Found suitable section",
            "parts": '[{"section":"Finance"}]',
            "assignToId": "a@co.com",
            "allowActions": ["APPROVE"],
            "nextSteps": "",
        }
        base.update(over)
        return base

    def test_happy_maps_records_to_step_objects(self):
        out = map_to_step_records([self._record()], self._sections())
        assert len(out) == 1
        assert out[0].message == "Found suitable section"

    def test_edge_blank_enum_fields_normalized_to_none(self):
        # grounded: enum fields that are "" / whitespace get set to None before from_dict
        rec = self._record(assignType="  ", allowActions=None)
        out = map_to_step_records([rec], self._sections())
        assert out[0].assignType is None

    def test_invalid_non_dict_record_raises(self):
        with pytest.raises(ValueError, match="is not a dict"):
            map_to_step_records(["not-a-dict"], self._sections())

    def test_failure_unmappable_record_wrapped(self):
        # from_dict fails on non-int stepId -> wrapped as "Failed to map record"
        bad = self._record(stepId="NaN")
        with pytest.raises(ValueError, match="Failed to map record"):
            map_to_step_records([bad], self._sections())

    def test_boundary_empty_records_returns_empty(self):
        assert map_to_step_records([], self._sections()) == []


# ---------------------------------------------------------------------------
# build_material_step_records  (async)
# ---------------------------------------------------------------------------
class TestBuildMaterialStepRecords:
    def _deps(self, llm_json='{"records": []}'):
        svc = MagicMock()
        svc.get_chat_completion = AsyncMock(return_value=llm_json)  # async collaborator
        settings = MagicMock()
        settings.AGENT_TYPE = "governance-schema"
        return {"open_ai_service": svc, "app_settings_adapter": settings}

    @pytest.mark.asyncio
    async def test_invalid_missing_open_ai_service_raises(self):
        # invalid input: no open_ai_service -> ValueError (grounded: explicit guard)
        state = {"message": "[]", "parameters": {"template_id": "t"}}
        with pytest.raises(ValueError, match="open_ai_service is required"):
            await build_material_step_records(state, {"open_ai_service": None})

    @pytest.mark.asyncio
    async def test_invalid_missing_template_id_raises(self):
        # message must parse to JSON first (json.loads before template_id check),
        # so pass a valid-JSON message with no template_id.
        state = {"message": "[]", "parameters": {}}
        with pytest.raises(ValueError, match="template_id is required"):
            await build_material_step_records(state, self._deps())

    @pytest.mark.asyncio
    async def test_happy_success_envelope(self):
        state = {
            "message": '[{"id": 1, "text": "approve Finance"}]',
            "parameters": {"template_id": "t"},
        }
        deps = self._deps(llm_json='{"records": []}')
        with patch(
            f"{_MOD}.fetch_section_definitions",
            new=AsyncMock(return_value=[SectionDefinition(section="Finance", fields=["vc"])]),
        ):
            out = await build_material_step_records(state, deps)
        assert out["transport_state"] == "RECEIVED"
        fr = out["formatted_response"]
        assert fr["status"] == "success"
        assert fr["agent_type"] == "governance-schema"
        assert fr["agent_data"]["records"] == []
        deps["open_ai_service"].get_chat_completion.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure_llm_error_wrapped_as_error_envelope(self):
        # failure path: get_chat_completion raises -> caught -> error envelope
        state = {
            "message": '[{"id": 1, "text": "approve Finance"}]',
            "parameters": {"template_id": "t"},
        }
        deps = self._deps()
        deps["open_ai_service"].get_chat_completion.side_effect = RuntimeError("llm down")
        with patch(
            f"{_MOD}.fetch_section_definitions",
            new=AsyncMock(return_value=[SectionDefinition(section="Finance", fields=["vc"])]),
        ):
            out = await build_material_step_records(state, deps)
        fr = out["formatted_response"]
        assert fr["status"] == "error"
        assert "llm down" in fr["message"]
        assert fr["agent_data"]["error"] == "llm down"

    @pytest.mark.asyncio
    async def test_edge_bad_message_json_raises_before_template_check(self):
        # edge: raw message that is not valid JSON -> json.loads raises (uncaught,
        # it is outside the try) -> propagates as JSONDecodeError (subclass of ValueError)
        state = {"message": "not-json", "parameters": {"template_id": "t"}}
        with pytest.raises(ValueError):
            await build_material_step_records(state, self._deps())
