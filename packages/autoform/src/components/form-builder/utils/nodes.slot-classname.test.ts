import { describe, expect, it } from "@rstest/core";
import type { DesignerContainer } from "../types";
import { fromISchema, toISchema } from "./nodes";

function tabsNode(slotClassName?: string): DesignerContainer {
  return {
    _id: "c1",
    _kind: "container",
    wrapperType: "TabWrapper",
    key: "tabs_1",
    outputType: "object",
    fieldConfig: {
      fieldWrapper: "TabWrapper",
      wrapperProps: { label: "Tabs", variant: "default", orientation: "horizontal" },
      controlProps: {},
      rules: [],
      condition: null,
    },
    children: [],
    slots: [
      { _id: "s1", label: "General Data", className: slotClassName, children: [] },
      { _id: "s2", label: "Pricing", children: [] },
    ],
  } as DesignerContainer;
}

describe("slot className", () => {
  it("is emitted into the tab item's wrapperProps", () => {
    const schema = toISchema([tabsNode("p-6 bg-muted")]);
    const firstTab = schema.fields[0].fields![0];

    expect(firstTab.fieldConfig.fieldWrapper).toBe("TabItemWrapper");
    expect(firstTab.fieldConfig.wrapperProps).toMatchObject({
      label: "General Data",
      className: "p-6 bg-muted",
    });
  });

  it("is omitted when unset, so no empty className pollutes the schema", () => {
    const schema = toISchema([tabsNode()]);
    const firstTab = schema.fields[0].fields![0];

    expect(firstTab.fieldConfig.wrapperProps).not.toHaveProperty("className");
  });

  it("survives a full export -> import round trip", () => {
    const schema = toISchema([tabsNode("p-6 bg-muted")]);
    const back = fromISchema(schema)[0] as DesignerContainer;

    expect(back.slots?.[0]?.className).toBe("p-6 bg-muted");
    expect(back.slots?.[0]?.label).toBe("General Data");
    expect(back.slots?.[1]?.className).toBeUndefined();
  });
});
