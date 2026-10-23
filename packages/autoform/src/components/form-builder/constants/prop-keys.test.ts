import { describe, expect, it } from "@rstest/core";
import {
  CONTAINER_WRAPPER_PROP_SCHEMA,
  CONTROL_PROP_KEYS,
  CONTROL_PROP_SCHEMA,
  ODATA_SLOT_NAMES,
  resolveContainerWrapperPropKeys,
  resolveContainerWrapperPropSchema,
  resolveControlPropKeys,
  resolveControlPropSchema,
  resolveFieldWrapperPropKeys,
} from "./prop-keys";

describe("prop-keys", () => {
  it("returns control keys for a known control, derived from CONTROL_PROP_SCHEMA when typed", () => {
    expect(resolveControlPropKeys("NumberControl")).toEqual(["placeholder", "min", "max", "readonly", "className"]);
  });

  it("resolveControlPropKeys derives keys from the typed schema when one exists", () => {
    // SA-2040 typed the last Advanced controls (Code/Upload/Button), so EVERY CONTROL_PROP_KEYS
    // control now also has a CONTROL_PROP_SCHEMA — the untyped fallback (CONTROL_PROP_KEYS[x]) is
    // now only a safety net for unknown/future controls. UploadControl (formerly the untyped
    // example — SelectControl before it, SA-2036) now resolves from its schema.
    expect(resolveControlPropKeys("UploadControl")).toEqual(
      (CONTROL_PROP_SCHEMA.UploadControl ?? []).map((p) => p.key),
    );
    // Coverage-completion invariant: no shipped control is left on the untyped path.
    for (const control of Object.keys(CONTROL_PROP_KEYS)) {
      expect(Object.keys(CONTROL_PROP_SCHEMA)).toContain(control);
    }
  });

  it("returns empty array for an unknown control", () => {
    expect(resolveControlPropKeys("MysteryControl")).toEqual([]);
  });

  it("maps odata parent to the odata FormItemWrapper variant", () => {
    expect(resolveFieldWrapperPropKeys("OdataWrapper")).toContain("align");
  });

  it("maps table parent to the table FormItemWrapper variant", () => {
    expect(resolveFieldWrapperPropKeys("TableWrapper")).toEqual(["label", "size", "minSize", "fixed"]);
  });

  it("falls back to base FormItemWrapper for no/other parent", () => {
    expect(resolveFieldWrapperPropKeys(null)).toEqual(["label", "description"]);
  });

  it("exposes the three odata slot names", () => {
    expect(ODATA_SLOT_NAMES).toEqual(["filter", "more-action", "row-more-action"]);
  });

  it("CONTROL_PROP_KEYS is a non-empty registry", () => {
    expect(Object.keys(CONTROL_PROP_KEYS).length).toBeGreaterThan(0);
  });
});

describe("honesty — Date/Time schema keys resolve to a read control key (SA-2038)", () => {
  for (const control of ["DatePickerControl", "DateTimePickerControl", "TimePickerControl"]) {
    it(`${control}: every typed schema key is also a known/read key`, () => {
      const schemaKeys = (CONTROL_PROP_SCHEMA[control] ?? []).map((p) => p.key);
      const readKeys = CONTROL_PROP_KEYS[control] ?? [];
      expect(schemaKeys.length).toBeGreaterThan(0);
      for (const k of schemaKeys) expect(readKeys).toContain(k);
    });
  }

  it("DateTimePickerControl reads `mode` after R4 (added to the read surface)", () => {
    expect(CONTROL_PROP_KEYS.DateTimePickerControl).toContain("mode");
  });
});

describe("CONTAINER_WRAPPER_PROP_SCHEMA — OdataWrapper", () => {
  const schema = resolveContainerWrapperPropSchema("OdataWrapper");
  const keys = schema.map((p) => p.key);

  it("is registered in CONTAINER_WRAPPER_PROP_SCHEMA", () => {
    expect(Object.keys(CONTAINER_WRAPPER_PROP_SCHEMA)).toContain("OdataWrapper");
  });

  it("exposes selectionMode, which the wrapper reads but the old list hid", () => {
    expect(keys).toContain("selectionMode");
  });

  it("drops the props no OdataWrapper code path reads", () => {
    expect(keys).not.toContain("data");
    expect(keys).not.toContain("rowCount");
    expect(keys).not.toContain("isShowRowMoreAction");
    expect(keys).not.toContain("endpoint");
  });

  it("types the six capability flags as booleans defaulting to the component's own defaults", () => {
    const byKey = Object.fromEntries(schema.map((p) => [p.key, p]));
    expect(byKey.isAdd).toMatchObject({ type: "boolean", default: true });
    expect(byKey.isEdit).toMatchObject({ type: "boolean", default: true });
    expect(byKey.isDelete).toMatchObject({ type: "boolean", default: true });
    expect(byKey.isShowSearch).toMatchObject({ type: "boolean", default: true });
    expect(byKey.isShowColumnSetting).toMatchObject({ type: "boolean", default: true });
    expect(byKey.readonly).toMatchObject({ type: "boolean", default: false });
  });

  it("types selectionMode as an enum with exactly the two legal values", () => {
    const sm = schema.find((p) => p.key === "selectionMode");
    expect(sm?.type).toBe("enum");
    expect(sm?.options).toEqual(["single", "multiple"]);
    expect(sm?.default).toBeUndefined(); // absence is a real state — never write "none"
  });

  it("resolveContainerWrapperPropKeys still works, derived from the schema", () => {
    expect(resolveContainerWrapperPropKeys("OdataWrapper")).toEqual(keys);
  });

  it("returns an empty schema for an unknown wrapper", () => {
    expect(resolveContainerWrapperPropSchema("MysteryWrapper")).toEqual([]);
  });
});

describe("CONTAINER_WRAPPER_PROP_SCHEMA — TableWrapper", () => {
  const schema = resolveContainerWrapperPropSchema("TableWrapper");
  const byKey = Object.fromEntries(schema.map((p) => [p.key, p]));
  const keys = schema.map((p) => p.key);

  it("exposes data as json — table-wrapper.tsx:77-83 resets the table from wrapperProps.data", () => {
    expect(keys).toContain("data");
    expect(byKey.data?.type).toBe("json");
  });

  it("exposes rowCount as a number — table-wrapper.tsx:70,78,86-88 read it", () => {
    expect(keys).toContain("rowCount");
    expect(byKey.rowCount?.type).toBe("number");
  });

  it("keeps readonly, which table-wrapper.tsx:37 really does read", () => {
    expect(byKey.readonly).toMatchObject({ type: "boolean", default: false });
  });

  it("exposes the add/delete flags the wrapper reads at lines 38-39", () => {
    expect(byKey.isAdd).toMatchObject({ type: "boolean", default: false });
    expect(byKey.isDelete).toMatchObject({ type: "boolean", default: false });
  });

  it("exposes the pagination props the wrapper reads at lines 71-72 with the hook's real defaults", () => {
    expect(byKey.pageSize).toMatchObject({ type: "number", default: 20 });
    expect(byKey.pageIndex).toMatchObject({ type: "number", default: 0 });
  });

  it("exposes enableSelection defaulting to true — table-wrapper.tsx:202", () => {
    expect(byKey.enableSelection).toMatchObject({ type: "boolean", default: true });
  });

  it("exposes search as json — table-wrapper.tsx:94 feeds it to useTableSearch", () => {
    expect(byKey.search?.type).toBe("json");
  });

  it("never declares a default it cannot honour for derived props", () => {
    // enablePagination (line 89) and isSyncServer (line 85) are derived at runtime.
    expect(byKey.enablePagination?.type).toBe("boolean");
    expect(byKey.enablePagination?.default).toBeUndefined();
    expect(byKey.isSyncServer?.type).toBe("boolean");
    expect(byKey.isSyncServer?.default).toBeUndefined();
  });

  it("omits the function-valued props no JSON editor can author", () => {
    expect(keys).not.toContain("callback");
    expect(keys).not.toContain("callApi");
  });
});

describe("CONTAINER_WRAPPER_PROP_SCHEMA — TabWrapper", () => {
  const schema = resolveContainerWrapperPropSchema("TabWrapper");
  const byKey = Object.fromEntries(schema.map((p) => [p.key, p]));
  const keys = schema.map((p) => p.key);

  it("is registered in CONTAINER_WRAPPER_PROP_SCHEMA", () => {
    expect(Object.keys(CONTAINER_WRAPPER_PROP_SCHEMA)).toContain("TabWrapper");
  });

  it("types variant as an enum of exactly the two tabsListVariants (ui/tabs.tsx:31-34)", () => {
    expect(byKey.variant?.type).toBe("enum");
    expect(byKey.variant?.options).toEqual(["default", "line"]);
    expect(byKey.variant?.default).toBe("default");
  });

  it("types orientation as an enum of exactly horizontal|vertical (tab-wrapper.tsx:40)", () => {
    expect(byKey.orientation?.type).toBe("enum");
    expect(byKey.orientation?.options).toEqual(["horizontal", "vertical"]);
    expect(byKey.orientation?.default).toBe("horizontal");
  });

  it("drops className — tab-wrapper.tsx:39 hardcodes it and never reads wrapperProps.className", () => {
    expect(keys).not.toContain("className");
  });

  it("regression guard: TabWrapper is a live palette item, so it must offer key suggestions", () => {
    expect(resolveContainerWrapperPropKeys("TabWrapper").length).toBeGreaterThan(0);
  });
});

describe("CONTROL_PROP_SCHEMA — Choice-lite (Checkbox/Switch)", () => {
  it("types CheckBoxControl with a single string `label` beside the box", () => {
    expect(resolveControlPropSchema("CheckBoxControl")).toEqual([
      { key: "label", type: "string", label: "Checkbox label" },
    ]);
  });

  it("types SwitchControl with a single string `label`", () => {
    expect(resolveControlPropSchema("SwitchControl")).toEqual([
      { key: "label", type: "string", label: "Switch label" },
    ]);
  });

  it("registers both under CONTROL_PROP_SCHEMA", () => {
    expect(Object.keys(CONTROL_PROP_SCHEMA)).toEqual(expect.arrayContaining(["CheckBoxControl", "SwitchControl"]));
  });
});

describe("CONTROL_PROP_SCHEMA — Choice-static scalars", () => {
  it("SelectControl offers placeholder and NEVER className (className is dead on Select)", () => {
    const keys = resolveControlPropSchema("SelectControl").map((p) => p.key);
    expect(keys).toEqual(["placeholder"]);
    expect(keys).not.toContain("className");
  });

  it("ComboboxControl offers placeholder + a boolean `multiple`", () => {
    const byKey = Object.fromEntries(resolveControlPropSchema("ComboboxControl").map((p) => [p.key, p]));
    expect(byKey.placeholder?.type).toBe("string");
    expect(byKey.multiple?.type).toBe("boolean");
  });

  it("RadioPopupControl offers className (RadioControl honours it via {...rest})", () => {
    const keys = resolveControlPropSchema("RadioPopupControl").map((p) => p.key);
    expect(keys).toContain("className");
  });
});

describe("CONTROL_PROP_SCHEMA — SelectTableControl (Choice-server)", () => {
  it("offers placeholder, multiple, selectAction, readOnly", () => {
    const byKey = Object.fromEntries(resolveControlPropSchema("SelectTableControl").map((p) => [p.key, p]));
    expect(byKey.placeholder?.type).toBe("string");
    expect(byKey.multiple?.type).toBe("boolean");
    expect(byKey.selectAction?.type).toBe("string");
    expect(byKey.readOnly?.type).toBe("boolean");
  });
});

describe("CONTROL_PROP_SCHEMA — Date/Time controls (SA-2038)", () => {
  it("is registered for all three controls", () => {
    expect(Object.keys(CONTROL_PROP_SCHEMA)).toEqual(
      expect.arrayContaining(["DatePickerControl", "DateTimePickerControl", "TimePickerControl"]),
    );
  });

  it("DatePickerControl.mode is a locked single|range enum defaulting to single", () => {
    const byKey = Object.fromEntries(resolveControlPropSchema("DatePickerControl").map((p) => [p.key, p]));
    expect(byKey.mode?.type).toBe("enum");
    expect(byKey.mode?.options).toEqual(["single", "range"]);
    expect(byKey.mode?.default).toBe("single");
    expect(byKey.placeholder?.type).toBe("string");
  });

  it("DateTimePickerControl locks mode=single|range, timeMode=12h|24h, and types the bounds as datetime", () => {
    const byKey = Object.fromEntries(resolveControlPropSchema("DateTimePickerControl").map((p) => [p.key, p]));
    expect(byKey.mode?.options).toEqual(["single", "range"]);
    expect(byKey.mode?.default).toBe("single");
    expect(byKey.timeMode?.options).toEqual(["12h", "24h"]);
    expect(byKey.timeMode?.default).toBe("24h");
    expect(byKey.minDateTime?.type).toBe("datetime");
    expect(byKey.maxDateTime?.type).toBe("datetime");
  });

  it("TimePickerControl uses mode=12h|24h (format, NOT single/range) and types the bounds as time", () => {
    const byKey = Object.fromEntries(resolveControlPropSchema("TimePickerControl").map((p) => [p.key, p]));
    expect(byKey.mode?.options).toEqual(["12h", "24h"]);
    expect(byKey.mode?.default).toBe("24h");
    expect(byKey.minTime?.type).toBe("time");
    expect(byKey.maxTime?.type).toBe("time");
  });

  it("returns an empty schema for an unknown control", () => {
    expect(resolveControlPropSchema("MysteryControl")).toEqual([]);
    expect(resolveControlPropSchema(undefined)).toEqual([]);
  });
});

describe("CONTROL_PROP_SCHEMA — CodeControl (SA-2040)", () => {
  const schema = resolveControlPropSchema("CodeControl");
  const byKey = Object.fromEntries(schema.map((p) => [p.key, p]));
  const keys = schema.map((p) => p.key);

  it("is registered", () => {
    expect(Object.keys(CONTROL_PROP_SCHEMA)).toContain("CodeControl");
  });

  it("types languageField as a field-ref — code-control.tsx useWatches a sibling", () => {
    expect(byKey.languageField?.type).toBe("field-ref");
  });

  it("types dark as an enum of exactly dark|light with no default (absence = follow app)", () => {
    expect(byKey.dark?.type).toBe("enum");
    expect(byKey.dark?.options).toEqual(["dark", "light"]);
    expect(byKey.dark?.default).toBeUndefined();
  });

  it("keeps the honoured LdcCodeEditor keys with the component's real height defaults", () => {
    expect(byKey.language?.type).toBe("string");
    expect(byKey.placeholder?.type).toBe("string");
    expect(byKey.readOnly).toMatchObject({ type: "boolean", default: false });
    expect(byKey.minHeight).toMatchObject({ type: "number", default: 160 });
    expect(byKey.maxHeight).toMatchObject({ type: "number", default: 480 });
  });

  it("offers no key the editor does not read", () => {
    expect(keys).not.toContain("options");
    expect(keys).not.toContain("multiple");
  });
});

describe("CONTROL_PROP_SCHEMA — UploadControl (SA-2040)", () => {
  const schema = resolveControlPropSchema("UploadControl");
  const byKey = Object.fromEntries(schema.map((p) => [p.key, p]));

  it("surfaces accept/maxSize/multiple the ui <Upload> honours but the old list hid", () => {
    expect(byKey.accept?.type).toBe("string");
    expect(byKey.maxSize).toMatchObject({ type: "number", default: 4194304 });
    expect(byKey.multiple).toMatchObject({ type: "boolean" });
  });

  it("keeps readOnly, which UploadControl itself reads", () => {
    expect(byKey.readOnly).toMatchObject({ type: "boolean", default: false });
  });
});

describe("CONTROL_PROP_SCHEMA — ButtonControl (SA-2040)", () => {
  const schema = resolveControlPropSchema("ButtonControl");
  const byKey = Object.fromEntries(schema.map((p) => [p.key, p]));
  const keys = schema.map((p) => p.key);

  it("types variant as an enum of exactly the six ui buttonVariants", () => {
    expect(byKey.variant?.type).toBe("enum");
    expect(byKey.variant?.options).toEqual(
      ["default", "destructive", "outline", "secondary", "ghost", "link"],
    );
  });

  it("types size as an enum whose members all exist on ui Button", () => {
    expect(byKey.size?.type).toBe("enum");
    expect(byKey.size?.options).toEqual(["default", "sm", "lg", "icon"]);
  });

  it("wires the action-DSL to the right widgets", () => {
    expect(byKey.icon?.type).toBe("icon");
    expect(byKey.action?.type).toBe("string");
    expect(byKey.actionPayload?.type).toBe("keyValue");
    expect(byKey.confirmBefore?.type).toBe("boolean");
    expect(byKey.loadingOnAction?.type).toBe("boolean");
    expect(byKey.watchField?.type).toBe("field-ref");
    expect(byKey.watchValue?.type).toBe("json");
  });

  it("drops className — not part of the typed cosmetic surface", () => {
    expect(keys).not.toContain("className");
  });
});
