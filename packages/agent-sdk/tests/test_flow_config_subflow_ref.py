from agent_sdk.layer1_domain.entities.flow_config import StepConfig


def test_step_config_has_subflow_ref_field():
    step = StepConfig(subflow_ref="my_subflow")
    assert step.subflow_ref == "my_subflow"


def test_step_config_to_dict_includes_subflow_ref():
    step = StepConfig(subflow_ref="x")
    result = step.to_dict()
    assert "subflow_ref" in result
    assert result["subflow_ref"] == "x"


def test_step_config_subflow_ref_defaults_to_empty_string():
    step = StepConfig()
    assert step.subflow_ref == ""
    assert step.to_dict()["subflow_ref"] == ""
