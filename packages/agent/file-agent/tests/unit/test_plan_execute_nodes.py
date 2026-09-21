# from __future__ import annotations

# import json

# from app.layer2_application.features.execute_agent.nodes.plan_execute_nodes import (
#     build_material_step_records,
# )


# def test_build_material_step_records_returns_expected_structure() -> None:
#     long_input = (
#         "1. Send to sara.le@company.com to contribute Procurement data (purchasing group, order unit). "
#         "2. Send to daniel.vo@company.com to contribute Supply Chain data (MRP type, lot size)."
#     )

#     result = build_material_step_records(long_input)

#     assert result["operation"] == "build_material_step_records"
#     assert len(result["records"]) == 10

#     first = result["records"][0]
#     assert first["templateId"] is None
#     assert first["description"] == long_input
#     assert first["stepId"] == 1
#     assert first["assignToId"] == "sara.le@company.com"
#     assert first["processType"] == "CONTRIBUTE"
#     assert first["nextSteps"] == "5,6"
#     assert first["parts"] == result["records"][1]["parts"]

#     ninth = result["records"][8]
#     assert ninth["skip"] is True
#     assert ninth["stepId"] == 9

#     parsed = json.loads(result["result"])
#     assert parsed[4]["assignToId"] == "maria.nguyen"