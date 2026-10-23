import { describe, expect, it } from "@rstest/core";
import type { ISchema } from "../../types/schema";
import { parseSchema } from "./form-builder-control";

const one: ISchema = {
  fields: [{ key: "a", outputType: "string", fieldConfig: { fieldControl: "InputControl" } }],
};

describe("parseSchema", () => {
  it("returns an empty schema for empty / whitespace / undefined", () => {
    expect(parseSchema("")).toEqual({ fields: [] });
    expect(parseSchema("   ")).toEqual({ fields: [] });
    expect(parseSchema(undefined)).toEqual({ fields: [] });
  });
  it("returns an empty schema for invalid JSON", () => {
    expect(parseSchema("not json")).toEqual({ fields: [] });
  });
  it("returns an empty schema when parsed JSON has no fields array", () => {
    expect(parseSchema(JSON.stringify({ foo: 1 }))).toEqual({ fields: [] });
  });
  it("parses a valid schema string", () => {
    expect(parseSchema(JSON.stringify(one)).fields).toHaveLength(1);
  });
  it("accepts an already-parsed schema object", () => {
    expect(parseSchema(one).fields).toHaveLength(1);
  });
  it("wraps a bare IField array (the stored input_schema shape)", () => {
    expect(parseSchema(JSON.stringify(one.fields)).fields).toHaveLength(1);
    expect(parseSchema(one.fields).fields).toHaveLength(1);
  });
});
