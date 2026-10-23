import { describe, expect, it } from "@rstest/core";
import { outputTypeForControlProps } from "./choice-output-type";

describe("outputTypeForControlProps", () => {
  it("Combobox: multiple → array, single → string", () => {
    expect(outputTypeForControlProps("ComboboxControl", { multiple: true }, "string")).toBe("array");
    expect(outputTypeForControlProps("ComboboxControl", { multiple: false }, "array")).toBe("string");
  });

  it("SelectTable: multiple → array, single → object", () => {
    expect(outputTypeForControlProps("SelectTableControl", { multiple: true }, "object")).toBe("array");
    expect(outputTypeForControlProps("SelectTableControl", {}, "array")).toBe("object");
  });

  it("controls that do not couple keep the fallback outputType", () => {
    expect(outputTypeForControlProps("SelectControl", { multiple: true }, "string")).toBe("string");
    expect(outputTypeForControlProps("RadioPopupControl", {}, "string")).toBe("string");
  });

  it("Date/DateTime: mode=range → object, mode=single (or unset) → string (SA-2038 Task 4)", () => {
    expect(outputTypeForControlProps("DatePickerControl", { mode: "range" }, "string")).toBe("object");
    expect(outputTypeForControlProps("DatePickerControl", { mode: "single" }, "object")).toBe("string");
    expect(outputTypeForControlProps("DatePickerControl", {}, "object")).toBe("string");
    expect(outputTypeForControlProps("DateTimePickerControl", { mode: "range" }, "string")).toBe("object");
    expect(outputTypeForControlProps("DateTimePickerControl", { mode: "single" }, "object")).toBe("string");
  });

  it("Time is NOT coupled — its `mode` is a 12h/24h format, so it keeps the fallback outputType", () => {
    expect(outputTypeForControlProps("TimePickerControl", { mode: "range" }, "string")).toBe("string");
    expect(outputTypeForControlProps("TimePickerControl", { mode: "12h" }, "string")).toBe("string");
  });
});
