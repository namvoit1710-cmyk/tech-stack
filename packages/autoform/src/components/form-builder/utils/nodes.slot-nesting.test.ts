import { describe, expect, it } from "@rstest/core";
import type { DesignerContainer } from "../types";
import { fromISchema, toISchema } from "./nodes";

describe("slot container nesting round-trip", () => {
  it("keeps an OdataWrapper container inside a Tab slot through toISchema → fromISchema", () => {
    const odata: DesignerContainer = {
      _id: "odata",
      _kind: "container",
      wrapperType: "OdataWrapper",
      key: "odata_1",
      outputType: "array",
      fieldConfig: { fieldWrapper: "OdataWrapper", wrapperProps: { label: "Table" } },
      children: [],
      columns: [],
    };
    const tab: DesignerContainer = {
      _id: "tab",
      _kind: "container",
      wrapperType: "TabWrapper",
      key: "tab_1",
      outputType: "object",
      fieldConfig: { fieldWrapper: "TabWrapper", wrapperProps: { label: "Tabs" } },
      children: [],
      slots: [{ _id: "slot1", label: "Tab 1", children: [odata] }],
    };

    const roundTripped = fromISchema(toISchema([tab]));

    const tabBack = roundTripped[0] as DesignerContainer;
    const slotChild = tabBack.slots?.[0]?.children?.[0] as DesignerContainer;
    expect(slotChild._kind).toBe("container");
    expect(slotChild.wrapperType).toBe("OdataWrapper");
  });
});
