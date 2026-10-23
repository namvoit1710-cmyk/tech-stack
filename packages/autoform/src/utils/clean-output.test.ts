import { describe, expect, it } from "@rstest/core";
import { cleanOutput } from "./clean-output";

describe("cleanOutput", () => {
    // happy
    it("strips top-level __-prefixed keys from an object", () => {
        expect(cleanOutput({ name: "a", __selected: true, __file_id: "x" })).toEqual({ name: "a" });
    });

    // happy — recursion into nested objects
    it("recursively strips __ keys from nested objects", () => {
        const input = {
            name: "a",
            __validate_status: "ok",
            child: { id: 1, __selected: false, deep: { v: 2, __x: 9 } },
        };
        expect(cleanOutput(input)).toEqual({ name: "a", child: { id: 1, deep: { v: 2 } } });
    });

    // happy — recursion into arrays of objects
    it("maps over arrays and cleans each element", () => {
        const input = [
            { a: 1, __selected: true },
            { b: 2, __validate_message: "err" },
        ];
        expect(cleanOutput(input)).toEqual([{ a: 1 }, { b: 2 }]);
    });

    // edge — array of primitives is returned as-is
    it("returns arrays of primitives unchanged", () => {
        expect(cleanOutput([1, "two", true, null])).toEqual([1, "two", true, null]);
    });

    // edge — empty object / empty array
    it("returns empty object and empty array unchanged", () => {
        expect(cleanOutput({})).toEqual({});
        expect(cleanOutput([])).toEqual([]);
    });

    // boundary — a key that is exactly "__"
    it('strips a key that is exactly "__"', () => {
        expect(cleanOutput({ __: 1, keep: 2 })).toEqual({ keep: 2 });
    });

    // edge — key with single leading underscore is kept (startsWith("__") is false)
    it("keeps keys with a single leading underscore", () => {
        expect(cleanOutput({ _id: "x", __hidden: 1 })).toEqual({ _id: "x" });
    });

    // invalid input / primitives — returned as-is
    it("returns primitives and null/undefined unchanged", () => {
        expect(cleanOutput("s")).toBe("s");
        expect(cleanOutput(42)).toBe(42);
        expect(cleanOutput(true)).toBe(true);
        expect(cleanOutput(null)).toBeNull();
        expect(cleanOutput(undefined)).toBeUndefined();
    });

    // edge — internal fields nested inside array items are stripped
    it("cleans arrays nested under kept keys", () => {
        const input = { rows: [{ v: 1, __selected: true }], __meta: {} };
        expect(cleanOutput(input)).toEqual({ rows: [{ v: 1 }] });
    });
});
