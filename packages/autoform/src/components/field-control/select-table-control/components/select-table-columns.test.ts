import { describe, expect, it } from "@rstest/core";
import { deriveColumns } from "./select-table-columns";

describe("deriveColumns", () => {
    it("returns empty array when rows is empty", () => {
        expect(deriveColumns([])).toEqual([]);
    });

    it("derives one column per key, excluding _id", () => {
        const cols = deriveColumns([{ _id: "1", id: "1", name: "Alice" }]);
        expect(cols.map((c) => c.accessorKey)).toEqual(["id", "name"]);
    });

    it("sets header equal to key name", () => {
        const cols = deriveColumns([{ _id: "1", id: "1", name: "Alice" }]);
        expect(cols[0]!.header).toBe("id");
        expect(cols[1]!.header).toBe("name");
    });

    it("cell renders value as string", () => {
        const cols = deriveColumns([{ _id: "1", score: 42 }]);
        const cell = cols[0]!.cell as (ctx: { getValue: () => unknown }) => string;
        expect(cell({ getValue: () => 42 })).toBe("42");
    });

    it("cell renders empty string for null value", () => {
        const cols = deriveColumns([{ _id: "1", score: null }]);
        const cell = cols[0]!.cell as (ctx: { getValue: () => unknown }) => string;
        expect(cell({ getValue: () => null })).toBe("");
    });

    it("cell renders empty string for undefined value", () => {
        const cols = deriveColumns([{ _id: "1", score: undefined }]);
        const cell = cols[0]!.cell as (ctx: { getValue: () => unknown }) => string;
        expect(cell({ getValue: () => undefined })).toBe("");
    });

    it("derives columns from first row only", () => {
        const cols = deriveColumns([
            { _id: "1", id: "1", name: "Alice" },
            { _id: "2", id: "2", name: "Bob", extra: "should-not-appear" },
        ]);
        expect(cols.map((c) => c.accessorKey)).toEqual(["id", "name"]);
    });

    it("includes all non-_id keys including numeric and boolean values", () => {
        const cols = deriveColumns([{ _id: "x", age: 30, active: true }]);
        expect(cols.map((c) => c.accessorKey)).toEqual(["age", "active"]);
    });
});
