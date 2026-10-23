import { describe, expect, it } from "@rstest/core";
import { resolveSelectedOutput } from "./resolve-selected-output";

const rows = [
    { _id: "a", v: 1 },
    { _id: "b", v: 2 },
    { _id: "c", v: 3 },
];

describe("resolveSelectedOutput — SA-1239 AC6 (selection output shape)", () => {
    it("single mode returns the chosen row as an object", () => {
        expect(resolveSelectedOutput(rows, { b: true }, "single")).toEqual({ _id: "b", v: 2 });
    });

    it("single mode returns null when nothing is selected", () => {
        expect(resolveSelectedOutput(rows, {}, "single")).toBeNull();
        expect(resolveSelectedOutput(rows, { b: false }, "single")).toBeNull();
    });

    it("multiple mode returns an array of the chosen rows", () => {
        expect(resolveSelectedOutput(rows, { a: true, c: true }, "multiple")).toEqual([
            { _id: "a", v: 1 },
            { _id: "c", v: 3 },
        ]);
    });

    it("multiple mode returns an empty array when nothing is selected", () => {
        expect(resolveSelectedOutput(rows, {}, "multiple")).toEqual([]);
    });

    it("falls back to id / ID when _id is absent", () => {
        expect(resolveSelectedOutput([{ id: "x", v: 9 }], { x: true }, "single")).toEqual({ id: "x", v: 9 });
        expect(resolveSelectedOutput([{ ID: "y", v: 8 }], { y: true }, "single")).toEqual({ ID: "y", v: 8 });
    });

    it("tolerates empty / missing inputs", () => {
        expect(resolveSelectedOutput([], { a: true }, "multiple")).toEqual([]);
        expect(resolveSelectedOutput([], { a: true }, "single")).toBeNull();
    });
});
