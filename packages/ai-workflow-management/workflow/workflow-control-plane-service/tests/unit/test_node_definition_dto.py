"""Tests for pydantic NodeDefinitionSummary / NodeDefinitionDetail contracts."""

import pytest
from pydantic import ValidationError

from app.layer2_application.contracts import (
    NodeDefinitionDetail,
    NodeDefinitionSummary,
    NodeProviderKind,
    NodeRuntime,
    NodeVisibility,
)


def _builtin_summary_dict() -> dict:
    return {
        "id": "builtin:condition",
        "name": "Condition",
        "description": "Evaluate a boolean expression.",
        "provider": "builtin",
        "nodeType": "CONDITION",
        "category": "control_flow",
        "runtime": "engine",
        "ports": {
            "in": [{"id": "in", "label": "In"}],
            "out": [
                {"id": "true_branch", "label": "True"},
                {"id": "false_branch", "label": "False"},
            ],
        },
    }


def _worker_summary_dict() -> dict:
    return {
        "id": "worker:http-worker",
        "name": "HTTP",
        "provider": "worker",
        "nodeType": "TASK",
        "category": "io.http",
        "runtime": "worker",
        "specVersion": "2.3.0",
    }


def _business_detail_dict() -> dict:
    return {
        "id": "business:abc",
        "name": "Send email",
        "provider": "business",
        "nodeType": "TASK",
        "runtime": "worker",
        "specVersion": "1.0.0",
        "workerType": "http-worker",
        "baseWorkerId": "http-worker",
        "lockedDefaults": {"url": "https://mail"},
        "inputMapping": {"body.to": "{{$input.to}}"},
        "inputSchema": [
            {
                "key": "to",
                "outputType": "string",
                "fieldConfig": {"fieldControl": "InputControl"},
            }
        ],
    }


class TestSummary:
    def test_summary_from_builtin(self):
        s = NodeDefinitionSummary.model_validate(_builtin_summary_dict())
        assert s.provider == "builtin"
        assert s.runtime == NodeRuntime.ENGINE
        assert s.spec_version == "1.0.0"
        assert s.visibility == NodeVisibility.VISIBLE

    def test_summary_from_worker(self):
        s = NodeDefinitionSummary.model_validate(_worker_summary_dict())
        assert s.provider == "worker"
        assert s.spec_version == "2.3.0"

    def test_summary_rejects_extra_fields(self):
        d = _builtin_summary_dict()
        d["unknown"] = "nope"
        with pytest.raises(ValidationError):
            NodeDefinitionSummary.model_validate(d)

    def test_summary_rejects_bad_provider(self):
        d = _builtin_summary_dict()
        d["provider"] = "nonsense"
        with pytest.raises(ValidationError):
            NodeDefinitionSummary.model_validate(d)

    def test_summary_serializes_to_camel(self):
        s = NodeDefinitionSummary.model_validate(_worker_summary_dict())
        out = s.model_dump(by_alias=True)
        assert "nodeType" in out
        assert "specVersion" in out


class TestDetail:
    def test_detail_from_business(self):
        d = NodeDefinitionDetail.model_validate(_business_detail_dict())
        assert d.provider == "business"
        assert d.worker_type == "http-worker"
        assert len(d.input_schema) == 1
        assert d.locked_defaults == {"url": "https://mail"}

    def test_detail_inherits_summary_fields(self):
        d = NodeDefinitionDetail.model_validate(_business_detail_dict())
        assert d.node_type == "TASK"
        assert d.runtime == NodeRuntime.WORKER

    def test_detail_rejects_extra_fields(self):
        d = _business_detail_dict()
        d["totally_unknown"] = 1
        with pytest.raises(ValidationError):
            NodeDefinitionDetail.model_validate(d)

    def test_detail_carries_credential_picker_metadata(self):
        d = _business_detail_dict()
        d["supportedCredentialKinds"] = ["API_KEY", "BASIC_AUTH"]
        d["credentialProvider"] = "GENERIC_HTTP"
        detail = NodeDefinitionDetail.model_validate(d)
        assert detail.supported_credential_kinds == ["API_KEY", "BASIC_AUTH"]
        assert detail.credential_provider == "GENERIC_HTTP"
        out = detail.model_dump(by_alias=True)
        assert out["supportedCredentialKinds"] == ["API_KEY", "BASIC_AUTH"]
        assert out["credentialProvider"] == "GENERIC_HTTP"

    def test_detail_credential_metadata_defaults_empty(self):
        d = NodeDefinitionDetail.model_validate(_business_detail_dict())
        assert d.supported_credential_kinds == []
        assert d.credential_provider is None

    def test_detail_validates_nested_form_field(self):
        d = _business_detail_dict()
        # Inject an invalid nested form field (SELECT without options).
        d["inputSchema"] = [{
            "key": "color",
            "outputType": "string",
            "fieldConfig": {"fieldControl": "SelectControl"},
        }]
        with pytest.raises(ValidationError, match="options"):
            NodeDefinitionDetail.model_validate(d)
