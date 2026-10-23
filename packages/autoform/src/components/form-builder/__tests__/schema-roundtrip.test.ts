import { describe, expect, it } from "@rstest/core";
import type { ISchema, OutputType } from "../../../types/schema";
import { createContainer, fromISchema, toISchema, uid } from "../utils/nodes";
import { createOdataWithSlotFields } from "../__mocks__/schema-fixtures";
import type { DesignerField, PaletteWrapperDef } from "../types";

const TAB_DEF: PaletteWrapperDef = {
  kind: "wrapper",
  wrapperType: "TabWrapper",
  label: "Tabs",
  description: "",
  isContainer: true,
  containerMode: "slots",
  defaultOutputType: "object",
};

describe("SA-1053 bug M — container round-trip preserves slot fields", () => {
  it("keeps OdataWrapper filter/more-action slot fields through import→export", () => {
    const odata = createOdataWithSlotFields();
    const schema1 = toISchema([odata]);
    const designer = fromISchema(schema1); // old import dropped `children` (slot fields) here
    const schema2 = toISchema(designer);

    const odataField = schema2.fields[0]!;
    // columns survive (inside the row template)
    expect(odataField.fields?.[0]?.fields?.[0]?.key).toBe("user_name");
    // slot fields survive — the bug M regression
    const slotFields = (odataField.fields ?? []).slice(1);
    expect(slotFields.map((f) => f.key)).toEqual(["filter_btn", "more_action_btn"]);
    expect(slotFields.map((f) => f.fieldConfig?.slot)).toEqual(["filter", "more-action"]);
  });

  it("always emits fields[0] as the row template even with zero columns", () => {
    const odata = createOdataWithSlotFields();
    odata.columns = []; // no columns, only slot fields
    const schema = toISchema([odata]);
    const odataField = schema.fields[0]!;
    // fields[0] is still the row template (empty), NOT a slot field — the positional contract
    expect(odataField.fields?.[0]?.key).toBe("odata_table_1_row");
    expect(odataField.fields?.[0]?.fields).toEqual([]);
    expect((odataField.fields ?? []).slice(1).map((f) => f.fieldConfig?.slot)).toEqual([
      "filter",
      "more-action",
    ]);
  });
});

describe("container default value round-trips (OData array + Tabs object)", () => {
  it("serialises + re-imports a container-level default", () => {
    const odata = createOdataWithSlotFields();
    odata.default = [{ user_name: "seed" }];
    const schema1 = toISchema([odata]);
    expect(schema1.fields[0]!.default).toEqual([{ user_name: "seed" }]);

    const schema2 = toISchema(fromISchema(schema1));
    expect(schema2.fields[0]!.default).toEqual([{ user_name: "seed" }]);
  });

  it("omits `default` from the schema when the container has none", () => {
    const odata = createOdataWithSlotFields();
    const schema = toISchema([odata]);
    expect(schema.fields[0]!).not.toHaveProperty("default");
  });
});

describe("SA-1053 bug K — tab data key is stable, not derived from the label", () => {
  it("does not derive the tab data key from the (renamed) label", () => {
    const tabs = createContainer(TAB_DEF);
    tabs.slots![0]!.label = "Price (USD)"; // rename — must NOT change the key
    const schema = toISchema([tabs]);
    const slot0 = schema.fields[0]!.fields![0]!;
    expect(slot0.key).toBe("tab_1"); // stable, not price_(usd)
    expect(slot0.fieldConfig?.wrapperProps?.label).toBe("Price (USD)"); // label still round-trips
  });

  it("preserves a hand-authored tab key across import→export", () => {
    const schema: ISchema = {
      fields: [
        {
          key: "tabs",
          outputType: "object",
          fieldConfig: { fieldWrapper: "TabWrapper", wrapperProps: { label: "Tabs" } },
          fields: [
            {
              key: "general",
              outputType: "object",
              fieldConfig: {
                fieldWrapper: "TabItemWrapper",
                wrapperProps: { label: "General Data" },
              },
              fields: [],
            },
          ],
        },
      ],
    };
    const out = toISchema(fromISchema(schema));
    expect(out.fields[0]!.fields![0]!.key).toBe("general"); // NOT general_data
  });

  it("sanitises + de-duplicates sibling tab keys on export (legacy keyless fallback)", () => {
    const tabs = createContainer(TAB_DEF);
    // two legacy slots with no key and a colliding, unsanitised label
    tabs.slots = [
      { _id: "a", key: "", label: "Price (USD)", children: [] },
      { _id: "b", key: "", label: "Price (USD)", children: [] },
    ];
    const schema = toISchema([tabs]);
    const keys = schema.fields[0]!.fields!.map((f) => f.key);
    expect(keys[0]).toBe("price_usd"); // sanitised: no parens
    expect(keys[1]).toBe("price_usd_1"); // uniqueness enforced
    expect(new Set(keys).size).toBe(2);
  });
});

describe("field-level default value round-trips (form-builder default was dropped on export)", () => {
  function makeInputField(
    defaultValue?: unknown,
    outputType: OutputType = "string"
  ): DesignerField {
    const node: DesignerField = {
      _id: uid(),
      _kind: "field",
      key: "input",
      outputType,
      fieldConfig: {
        fieldWrapper: "FormItemWrapper",
        fieldControl: "InputControl",
        wrapperProps: { label: "Text Input" },
        controlProps: {},
        rules: [],
        condition: null,
      },
    };
    if (defaultValue !== undefined) node.default = defaultValue;
    return node;
  }

  it("serialises a plain field's default", () => {
    const schema = toISchema([makeInputField("abc")]);
    expect(schema.fields[0]!.default).toBe("abc");
  });

  it("survives export -> import -> export", () => {
    const schema1 = toISchema([makeInputField("abc")]);
    const schema2 = toISchema(fromISchema(schema1));
    expect(schema2.fields[0]!.default).toBe("abc");
  });

  it("omits `default` when the field has none", () => {
    const schema = toISchema([makeInputField()]);
    expect(schema.fields[0]!).not.toHaveProperty("default");
  });

  it("keeps a falsy default rather than dropping it", () => {
    const zero = toISchema([makeInputField(0, "number")]);
    expect(zero.fields[0]!.default).toBe(0);

    const empty = toISchema([makeInputField("")]);
    expect(empty.fields[0]!.default).toBe("");
  });

  it("keeps a column default inside an OdataWrapper row template", () => {
    const odata = createOdataWithSlotFields();
    odata.columns![0]!.default = "seed";
    const schema = toISchema([odata]);
    const rowTemplate = schema.fields[0]!.fields![0]!;
    expect(rowTemplate.fields?.[0]?.default).toBe("seed");
  });

  it("keeps a slot field's default", () => {
    const odata = createOdataWithSlotFields();
    odata.children![0]!.default = "slot-seed";
    const schema = toISchema([odata]);
    const slotField = schema.fields[0]!.fields!.slice(1)[0]!;
    expect(slotField.default).toBe("slot-seed");
  });
});
