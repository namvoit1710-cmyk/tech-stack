import { describe, expect, it } from "@rstest/core";
import { RULE_METHODS, coerceRuleValue, isRuleEnforced, ruleTakesValue, ruleValueKind } from "./rules-utils";

describe("rules-utils", () => {
  it("lists all validation methods including required", () => {
    expect(RULE_METHODS).toContain("required");
    expect(RULE_METHODS).toContain("multipleOf");
  });

  it("value-less methods take no value", () => {
    for (const m of ["required", "trim", "int", "positive", "negative"] as const) {
      expect(ruleTakesValue(m)).toBe(false);
    }
  });

  it("refine/superRefine now TAKE a value — the expression the runtime evaluates", () => {
    expect(ruleTakesValue("refine")).toBe(true);
    expect(ruleTakesValue("superRefine")).toBe(true);
    expect(ruleValueKind("refine")).toBe("expression");
  });

  it("min/max/pattern take a value", () => {
    expect(ruleTakesValue("min")).toBe(true);
    expect(ruleTakesValue("pattern")).toBe(true);
  });

  it("classifies the value input kind per method", () => {
    expect(ruleValueKind("required")).toBe("none");
    expect(ruleValueKind("min")).toBe("number");
    expect(ruleValueKind("gte")).toBe("number");
    expect(ruleValueKind("pattern")).toBe("text");
    expect(ruleValueKind("includes")).toBe("text");
  });

  it("marks only the runtime-honored methods as enforced (the panel must not lie)", () => {
    expect(isRuleEnforced("required")).toBe(true);
    expect(isRuleEnforced("refine")).toBe(true);
    expect(isRuleEnforced("min")).toBe(false);
    expect(isRuleEnforced("pattern")).toBe(false);
    expect(isRuleEnforced("superRefine")).toBe(false);
  });

  it("coerces numeric methods to numbers", () => {
    expect(coerceRuleValue("min", "3")).toBe(3);
    expect(coerceRuleValue("gte", "10")).toBe(10);
  });

  it("keeps string + expression methods as strings", () => {
    expect(coerceRuleValue("pattern", "^a.*$")).toBe("^a.*$");
    expect(coerceRuleValue("refine", "value > 5")).toBe("value > 5");
  });
});
