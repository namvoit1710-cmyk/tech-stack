import { describe, expect, it } from "@rstest/core";
import type { IField } from "../types/schema";
import { getDefaultValues, normalizeArrayTableValues } from "./helpers";

// minimal field factory (fieldConfig is required by the type)
const field = (partial: Partial<IField> & Pick<IField, "key" | "outputType">): IField => ({
    fieldConfig: {},
    ...partial,
});

describe("getDefaultValues", () => {
    // edge — no fields / default arg
    it("returns an empty object for an empty or omitted field list", () => {
        expect(getDefaultValues([])).toEqual({});
        expect(getDefaultValues()).toEqual({});
    });

    // happy — string field with a default
    it("uses the provided default for a primitive field", () => {
        const fields = [field({ key: "name", outputType: "string", default: "hello" })];
        expect(getDefaultValues(fields)).toEqual({ name: "hello" });
    });

    it("omits a primitive field only when default is absent", () => {
        const fields = [field({ key: "a", outputType: "string" })];
        expect(getDefaultValues(fields)).toEqual({});
    });

    it("keeps a falsy primitive default", () => {
        const fields = [
            field({ key: "b", outputType: "number", default: 0 }),
            field({ key: "c", outputType: "string", default: "" }),
            field({ key: "d", outputType: "boolean", default: false }),
            field({ key: "e", outputType: "string", default: null }),
        ];
        expect(getDefaultValues(fields)).toEqual({ b: 0, c: "", d: false, e: null });
    });

    // happy — object field with explicit default takes precedence
    it("uses explicit default for an object field when present", () => {
        const fields = [
            field({ key: "obj", outputType: "object", default: { x: 1 }, fields: [] }),
        ];
        expect(getDefaultValues(fields)).toEqual({ obj: { x: 1 } });
    });

    // happy — object field without default recurses into children
    it("recurses into children for an object field without a default", () => {
        const fields = [
            field({
                key: "parent",
                outputType: "object",
                fields: [field({ key: "child", outputType: "string", default: "v" })],
            }),
        ];
        expect(getDefaultValues(fields)).toEqual({ parent: { child: "v" } });
    });

    // boundary — object field, no default, no children → nested empty object
    it("yields an empty object for an object field with no default and no children", () => {
        const fields = [field({ key: "empty", outputType: "object" })];
        expect(getDefaultValues(fields)).toEqual({ empty: {} });
    });

    // happy — array field with a default
    it("uses default for an array field", () => {
        const fields = [field({ key: "arr", outputType: "array", default: [1, 2] })];
        expect(getDefaultValues(fields)).toEqual({ arr: [1, 2] });
    });

    // edge — array field with no default is omitted
    it("omits an array field with no default", () => {
        const fields = [field({ key: "arr", outputType: "array" })];
        expect(getDefaultValues(fields)).toEqual({});
    });

    it("keeps an explicit empty array default", () => {
        const fields = [field({ key: "arr", outputType: "array", default: [] })];
        expect(getDefaultValues(fields)).toEqual({ arr: [] });
    });

    it("seeds an OData row form from its column defaults, falsy included", () => {
        const columns = [
            field({ key: "input", outputType: "string", default: "" }),
            field({ key: "qty", outputType: "number", default: 0 }),
            field({ key: "active", outputType: "boolean", default: false }),
        ];
        expect(getDefaultValues(columns)).toEqual({ input: "", qty: 0, active: false });
    });
});

describe("normalizeArrayTableValues", () => {
    const arrayTableField = (key: string, nameKey: string, valueKey: string): IField =>
        field({
            key,
            outputType: "array",
            fieldConfig: { fieldWrapper: "TableRowWrapper" },
            fields: [
                field({
                    key: "item",
                    outputType: "object",
                    fields: [
                        field({ key: nameKey, outputType: "string" }),
                        field({ key: valueKey, outputType: "string" }),
                    ],
                }),
            ],
        });

    // invalid input — non-object values returned unchanged
    it("returns non-plain-object values unchanged", () => {
        expect(normalizeArrayTableValues([], "str")).toBe("str");
        expect(normalizeArrayTableValues([], 5)).toBe(5);
        expect(normalizeArrayTableValues([], null)).toBeNull();
        expect(normalizeArrayTableValues([], [1, 2])).toEqual([1, 2]);
    });

    // happy — dict value of a TableRowWrapper array field is unfolded to rows
    it("unfolds a dict into name/value rows for a TableRowWrapper array field", () => {
        const fields = [arrayTableField("input_mapping", "param_name", "param_value")];
        const values = { input_mapping: { crId: "{{$x}}", region: "EU" } };
        expect(normalizeArrayTableValues(fields, values)).toEqual({
            input_mapping: [
                { param_name: "crId", param_value: "{{$x}}" },
                { param_name: "region", param_value: "EU" },
            ],
        });
    });

    // edge — does not mutate the input object
    it("returns a shallow clone and does not mutate input", () => {
        const fields = [arrayTableField("m", "n", "v")];
        const values = { m: { a: 1 } };
        const out = normalizeArrayTableValues(fields, values) as Record<string, unknown>;
        expect(out).not.toBe(values);
        expect(values.m).toEqual({ a: 1 }); // original dict untouched
    });

    // edge — null/undefined field value is skipped (continue on value == null)
    it("skips fields whose value is null or undefined", () => {
        const fields = [arrayTableField("m", "n", "v")];
        const values = { m: null, keep: 1 };
        expect(normalizeArrayTableValues(fields, values)).toEqual({ m: null, keep: 1 });
    });

    // edge — already-array value is left as-is (not a plain object → no unfold)
    it("leaves an already-array table value unchanged", () => {
        const fields = [arrayTableField("m", "n", "v")];
        const values = { m: [{ n: "x", v: "y" }] };
        expect(normalizeArrayTableValues(fields, values)).toEqual({ m: [{ n: "x", v: "y" }] });
    });

    // failure path / guard — array field WITHOUT TableRowWrapper is not unfolded
    it("does not unfold when fieldWrapper is not TableRowWrapper", () => {
        const f = arrayTableField("m", "n", "v");
        f.fieldConfig = { fieldWrapper: "OdataWrapper" };
        const values = { m: { a: 1 } };
        expect(normalizeArrayTableValues([f], values)).toEqual({ m: { a: 1 } });
    });

    // boundary — item field pair missing → no unfold (guard on nameField/valueField)
    it("does not unfold when the item field pair is missing", () => {
        const f = field({
            key: "m",
            outputType: "array",
            fieldConfig: { fieldWrapper: "TableRowWrapper" },
            fields: [field({ key: "item", outputType: "object", fields: [] })],
        });
        const values = { m: { a: 1 } };
        expect(normalizeArrayTableValues([f], values)).toEqual({ m: { a: 1 } });
    });

    // happy — recurses through nested object fields
    it("recurses through object fields", () => {
        const fields = [
            field({
                key: "group",
                outputType: "object",
                fields: [arrayTableField("mapping", "n", "v")],
            }),
        ];
        const values = { group: { mapping: { k1: "v1" } } };
        expect(normalizeArrayTableValues(fields, values)).toEqual({
            group: { mapping: [{ n: "k1", v: "v1" }] },
        });
    });
});
