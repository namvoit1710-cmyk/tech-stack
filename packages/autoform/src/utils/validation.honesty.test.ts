import { describe, expect, it } from "@rstest/core";
import { RUNTIME_HONORED_METHODS } from "./validation";

describe("validation — runtime honored methods (honesty source of truth)", () => {
  it("honors exactly the methods that have a handler: required + refine", () => {
    expect([...RUNTIME_HONORED_METHODS].sort()).toEqual(["refine", "required"]);
  });

  it("does NOT claim to honor the unwired methods", () => {
    expect(RUNTIME_HONORED_METHODS).not.toContain("min");
    expect(RUNTIME_HONORED_METHODS).not.toContain("pattern");
  });
});
