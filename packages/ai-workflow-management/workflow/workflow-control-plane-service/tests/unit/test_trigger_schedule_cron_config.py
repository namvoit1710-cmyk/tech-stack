"""Regression: a TRIGGER node's ``schedule_cron`` must land in engine ``config``.

The frontend sends node fields under ``parameters``; the spec-driven
``split_parameters`` routes declared config keys into engine ``config`` (the
rest go to ``input_binding``). The scheduler reads
``node.config["schedule_cron"]``, so the TRIGGER spec must declare it.
"""
from app.layer1_domain.entities.workflow import WorkflowNode
from app.layer1_domain.node_spec import get_spec
from app.layer1_domain.node_spec.parameter_mapper import split_parameters
from app.layer1_domain.value_objects.node_type import NodeType


def test_split_parameters_routes_schedule_cron_to_config():
    config, binding = split_parameters(
        get_spec(NodeType.TRIGGER),
        {"trigger_type": "schedule", "schedule_cron": "0 9 * * *"},
    )
    assert config == {"trigger_type": "schedule", "schedule_cron": "0 9 * * *"}
    assert "schedule_cron" not in binding


def test_workflow_node_post_init_splits_schedule_cron_into_config():
    node = WorkflowNode(
        id="trigger",
        node_type=NodeType.TRIGGER,
        parameters={"trigger_type": "schedule", "schedule_cron": "*/5 * * * *"},
    )
    assert node.config.get("schedule_cron") == "*/5 * * * *"
    assert node.config.get("trigger_type") == "schedule"
