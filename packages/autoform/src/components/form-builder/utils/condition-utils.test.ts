import { describe, expect, it } from "@rstest/core";
import { EShowWhenOperator } from "../../../types/condition";
import { coerceConditionValue, conditionValueKind } from "./condition-utils";

describe("condition-utils — value kind is typed by operator + watched-field outputType", () => {
  it("exists/notExists take NO value", () => {
    expect(conditionValueKind(EShowWhenOperator.Exists, "string")).toBe("none");
    expect(conditionValueKind(EShowWhenOperator.NotExists, "number")).toBe("none");
  });

  it("`in` takes a LIST (runtime needs Array.isArray)", () => {
    expect(conditionValueKind(EShowWhenOperator.In, "string")).toBe("list");
    expect(conditionValueKind(EShowWhenOperator.In, "number")).toBe("list");
  });

  it("comparison operators force a number regardless of field type", () => {
    expect(conditionValueKind(EShowWhenOperator.GreaterThan, "string")).toBe("number");
    expect(conditionValueKind(EShowWhenOperator.LessOrEqual, "string")).toBe("number");
  });

  it("equals/notEquals/contains are typed by the watched field's outputType", () => {
    expect(conditionValueKind(EShowWhenOperator.Equals, "number")).toBe("number");
    expect(conditionValueKind(EShowWhenOperator.Equals, "boolean")).toBe("boolean");
    expect(conditionValueKind(EShowWhenOperator.Equals, "string")).toBe("text");
    expect(conditionValueKind(EShowWhenOperator.Contains, "string")).toBe("text");
  });
});

describe("condition-utils — coercion makes the runtime comparators fire", () => {
  it("coerces a number-field `equals` value to a real number so `===` matches", () => {
    expect(coerceConditionValue(EShowWhenOperator.Equals, "number", "5")).toBe(5);
  });

  it("coerces a boolean-field value to a real boolean", () => {
    expect(coerceConditionValue(EShowWhenOperator.Equals, "boolean", "true")).toBe(true);
    expect(coerceConditionValue(EShowWhenOperator.Equals, "boolean", "false")).toBe(false);
  });

  it("builds an ARRAY for `in`, coercing each element by field type", () => {
    expect(coerceConditionValue(EShowWhenOperator.In, "number", ["1", "2"])).toEqual([1, 2]);
    expect(coerceConditionValue(EShowWhenOperator.In, "string", ["a", "b"])).toEqual(["a", "b"]);
  });

  it("drops the value entirely for exists/notExists", () => {
    expect(coerceConditionValue(EShowWhenOperator.Exists, "string", "x")).toBeUndefined();
    expect(coerceConditionValue(EShowWhenOperator.NotExists, "string", "x")).toBeUndefined();
  });

  it("keeps a string value as a string for a plain text field", () => {
    expect(coerceConditionValue(EShowWhenOperator.Equals, "string", "active")).toBe("active");
  });
});
