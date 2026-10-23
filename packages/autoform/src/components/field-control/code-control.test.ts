import { describe, expect, it } from "@rstest/core";
import { toEditorValue } from "./code-control";

describe("toEditorValue (Monaco needs a string)", () => {
    it("passes strings through unchanged", () => {
        expect(toEditorValue("return items;")).toBe("return items;");
        expect(toEditorValue("")).toBe("");
    });

    it("treats null/undefined as empty (not the text 'null')", () => {
        expect(toEditorValue(null)).toBe("");
        expect(toEditorValue(undefined)).toBe("");
    });

    it("renders arrays/objects as pretty JSON — the Input Mapping 'Try it' crash repro", () => {
        // A prior test run stored arr1 as a real array in localStorage; the field is
        // now a JSON CodeControl, so the array must become a string, not crash Monaco.
        expect(toEditorValue([1, 2])).toBe("[\n  1,\n  2\n]");
        expect(toEditorValue({ a: 1 })).toBe("{\n  \"a\": 1\n}");
    });

    it("never throws on a circular value", () => {
        const a: any = {};
        a.self = a;
        expect(typeof toEditorValue(a)).toBe("string");
    });
});
