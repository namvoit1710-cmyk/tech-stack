import { describe, expect, it } from "@rstest/core";
import { toISchema } from "../utils/nodes";
import { createOdataWithSlotFields } from "../__mocks__/schema-fixtures";

describe("schema export — OdataWrapper with slot fields", () => {
  it("wraps columns in a row template and appends slot fields with slot preserved", () => {
    const odata = createOdataWithSlotFields();
    const schema = toISchema([odata]);

    expect(schema.fields).toHaveLength(1);
    const odataField = schema.fields[0]!;
    expect(odataField.fields).toBeDefined();

    // fields[0] is the row template (FragmentWrapper) whose children are the columns.
    // This is the contract the runtime relies on: use-column-visibility reads
    // `field.fields[0].fields` to build the table columns.
    const rowTemplate = odataField.fields![0]!;
    expect(rowTemplate.key).toBe("odata_table_1_row");
    expect(rowTemplate.fieldConfig?.fieldWrapper).toBe("FragmentWrapper");
    expect(rowTemplate.fields?.[0]?.key).toBe("user_name");
    expect(rowTemplate.fields?.[0]?.outputType).toBe("string");

    // Remaining fields are the slot fields (filter + more-action), slot property preserved.
    const slotFields = odataField.fields!.slice(1);
    expect(slotFields).toHaveLength(2);

    const filterField = slotFields[0]!;
    expect(filterField.key).toBe("filter_btn");
    expect(filterField.fieldConfig?.slot).toBe("filter");

    const moreActionField = slotFields[1]!;
    expect(moreActionField.key).toBe("more_action_btn");
    expect(moreActionField.fieldConfig?.slot).toBe("more-action");
  });

  it("preserves distinct slot properties for multiple slot types", () => {
    const odata = createOdataWithSlotFields();
    const schema = toISchema([odata]);

    const odataField = schema.fields[0]!;
    const slotFields = odataField.fields?.slice(1) ?? [];

    // Verify slot properties are distinct
    const slots = slotFields.map((f) => f.fieldConfig?.slot).filter(Boolean);
    expect(slots).toEqual(expect.arrayContaining(["filter", "more-action"]));
    expect(new Set(slots).size).toBe(2); // Each slot appears exactly once
  });
});
