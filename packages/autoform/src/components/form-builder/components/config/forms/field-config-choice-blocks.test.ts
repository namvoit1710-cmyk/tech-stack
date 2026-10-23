import { describe, expect, it } from "@rstest/core";
import type { DesignerNode } from "../../../types";
import { deriveActionAndSiblings } from "./field-config-choice-blocks";

const field = (id: string, key: string, outputType: string): DesignerNode =>
  ({ _id: id, _kind: "field", key, outputType, fieldConfig: {} } as unknown as DesignerNode);

describe("deriveActionAndSiblings", () => {
  it("returns each sibling FIELD with its key and outputType (for the condition watch dropdown)", () => {
    const self = field("a", "amount", "number");
    const sibling = field("b", "status", "string");
    const { siblingFields, siblingFieldKeys } = deriveActionAndSiblings(self, [self, sibling], false);
    expect(siblingFields).toEqual([{ key: "status", outputType: "string" }]);
    expect(siblingFieldKeys).toEqual(["status"]);
  });
});
