import { describe, expect, it } from "@rstest/core";
import { findParentContainer } from "./nodes";
import type { DesignerContainer, DesignerField } from "../types";

const field = (id: string): DesignerField => ({
  _id: id,
  _kind: "field",
  key: id,
  outputType: "string",
  fieldConfig: {},
});

const odata = (id: string, columns: DesignerField[]): DesignerContainer => ({
  _id: id,
  _kind: "container",
  wrapperType: "OdataWrapper",
  key: id,
  outputType: "array",
  fieldConfig: {},
  children: [],
  columns,
});

describe("findParentContainer", () => {
  it("returns null for a root-level node", () => {
    const root = field("a");
    expect(findParentContainer([root], "a")).toBeNull();
  });

  it("returns the container holding a nested field", () => {
    const child = field("c1");
    const container = odata("t1", [child]);
    const parent = findParentContainer([container], "c1");
    expect(parent?._id).toBe("t1");
    expect(parent?.wrapperType).toBe("OdataWrapper");
  });

  it("returns null for an unknown id", () => {
    expect(findParentContainer([field("a")], "zzz")).toBeNull();
  });
});
