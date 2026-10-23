import { describe, expect, it } from "@rstest/core";
import type { IField } from "../types/schema";
import type { IValidationRules } from "../types/validation";
import { createZodSchema } from "./validation";

const field = (partial: Partial<IField> & Pick<IField, "key" | "outputType">): IField => ({
    fieldConfig: {},
    ...partial,
});

const withRules = (
    key: string,
    outputType: IField["outputType"],
    rules: IValidationRules[],
    extra: Partial<IField> = {},
): IField => field({ key, outputType, fieldConfig: { rules }, ...extra });

describe("createZodSchema", () => {
    // edge — empty / omitted schema → empty loose object accepts anything
    it("builds a schema from an empty field list", () => {
        const schema = createZodSchema([]);
        expect(schema.safeParse({}).success).toBe(true);
        // loose object → unknown keys pass through
        expect(schema.safeParse({ extra: 1 }).success).toBe(true);
    });

    // happy — optional field (no rules) accepts value, null, and absent
    it("makes a field with no rules nullable + optional", () => {
        const schema = createZodSchema([field({ key: "a", outputType: "string" })]);
        expect(schema.safeParse({ a: "x" }).success).toBe(true);
        expect(schema.safeParse({ a: null }).success).toBe(true);
        expect(schema.safeParse({}).success).toBe(true);
    });

    // happy — required string uses min(1)
    it("enforces required on a string via min(1)", () => {
        const schema = createZodSchema([
            withRules("name", "string", [{ method: "required", message: "req" } as IValidationRules]),
        ]);
        expect(schema.safeParse({ name: "ok" }).success).toBe(true);
        const empty = schema.safeParse({ name: "" });
        expect(empty.success).toBe(false);
    });

    // invalid input — required non-string rejects null/undefined/empty-array via refine
    it("enforces required on non-string types via refine predicate", () => {
        const schema = createZodSchema([
            withRules("arr", "array", [{ method: "required", message: "req" } as IValidationRules], {
                // ensure it stays z.any() path: no fields → z.any()
            }),
        ]);
        expect(schema.safeParse({ arr: [1] }).success).toBe(true);
        expect(schema.safeParse({ arr: null }).success).toBe(false);
        expect(schema.safeParse({ arr: [] }).success).toBe(false);
        expect(schema.safeParse({}).success).toBe(false); // undefined fails required refine
    });

    // failure path — refine rule with a runtime expression
    it("applies a refine rule from a value expression", () => {
        const schema = createZodSchema([
            withRules("n", "number", [
                { method: "refine", value: "value > 5", message: "too small" } as IValidationRules,
            ]),
        ]);
        expect(schema.safeParse({ n: 10 }).success).toBe(true);
        expect(schema.safeParse({ n: 1 }).success).toBe(false);
    });

    // failure path — refine expression that throws returns false (caught → invalid)
    it("treats a throwing refine expression as invalid (catch → false)", () => {
        const schema = createZodSchema([
            withRules("n", "string", [
                { method: "refine", value: "value.nope.deep", message: "bad" } as IValidationRules,
            ]),
        ]);
        // "value.nope.deep" throws on a string value → caught → false → invalid
        expect(schema.safeParse({ n: "abc" }).success).toBe(false);
    });

    // edge — unknown rule method is ignored (no handler)
    it("ignores an unknown rule method", () => {
        const schema = createZodSchema([
            withRules("a", "string", [{ method: "banana", message: "x" } as unknown as IValidationRules]),
        ]);
        // no required → nullable/optional; unknown handler is skipped
        expect(schema.safeParse({ a: "x" }).success).toBe(true);
        expect(schema.safeParse({}).success).toBe(true);
    });

    // boundary — array field with object subfield builds a nested array schema
    it("builds an array-of-object schema for array fields with object subfield", () => {
        const schema = createZodSchema([
            field({
                key: "rows",
                outputType: "array",
                fields: [
                    field({
                        key: "row",
                        outputType: "object",
                        fields: [
                            withRules("city", "string", [
                                { method: "required", message: "city required" } as IValidationRules,
                            ]),
                        ],
                    }),
                ],
            }),
        ]);
        expect(schema.safeParse({ rows: [{ city: "NYC" }] }).success).toBe(true);
        expect(schema.safeParse({ rows: [{ city: "" }] }).success).toBe(false);
    });

    // edge — OdataWrapper array field short-circuits to z.any()
    it("uses z.any() for an OdataWrapper array field", () => {
        const schema = createZodSchema([
            field({
                key: "od",
                outputType: "array",
                fieldConfig: { fieldWrapper: "OdataWrapper" },
                fields: [field({ key: "x", outputType: "object", fields: [] })],
            }),
        ]);
        // any → anything (including a plain object) passes; nullable/optional as no rules
        expect(schema.safeParse({ od: { anything: true } }).success).toBe(true);
    });

    // happy — object field with children builds a nested loose object
    it("builds a nested object schema for object fields with children", () => {
        const schema = createZodSchema([
            field({
                key: "addr",
                outputType: "object",
                fields: [
                    withRules("zip", "string", [
                        { method: "required", message: "zip required" } as IValidationRules,
                    ]),
                ],
            }),
        ]);
        expect(schema.safeParse({ addr: { zip: "10001" } }).success).toBe(true);
        expect(schema.safeParse({ addr: { zip: "" } }).success).toBe(false);
    });
});
