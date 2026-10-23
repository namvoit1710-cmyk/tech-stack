import { describe, expect, it } from "@rstest/core";
import { FieldControl } from "../../field-control";
import { CONTROL_META, FIELD_DEFS, WRAPPER_DEFS } from "./palette";

describe("palette", () => {
  it("CONTROL_META covers every FieldControl key explicitly", () => {
    const missing = Object.keys(FieldControl).filter((k) => !(k in CONTROL_META));
    expect(missing).toEqual([]);
  });

  it("exposes exactly OdataWrapper and TabWrapper", () => {
    const wrapperKeys = WRAPPER_DEFS.map((w) => w.wrapperType).sort();
    expect(wrapperKeys).toEqual(["OdataWrapper", "TabWrapper"]);
  });

  it("every field def carries an output type and category", () => {
    for (const d of FIELD_DEFS) {
      expect(d.defaultOutputType).toBeTruthy();
      expect(d.category).toBeTruthy();
    }
  });

  it("registers the Form Builder control under Advanced", () => {
    expect(CONTROL_META.FormBuilderControl).toEqual({
      label: "Form Builder",
      category: "Advanced",
      defaultOutputType: "string",
    });
  });

  it("exposes a Form Builder field def in the Advanced category", () => {
    const def = FIELD_DEFS.find((d) => d.type === "FormBuilderControl");
    expect(def?.category).toBe("Advanced");
    expect(def?.defaultOutputType).toBe("string");
  });
});
