import { describe, expect, it } from "@rstest/core";

import { validateEditorValue } from "./validate-editor-value";

// SOURCE: validate-editor-value.ts
//   Returns false unless value is a non-null object with array `nodes` and
//   array `connections`. Every node must have string `id` and object `data`
//   (typeof null === "object" so data:null passes this check). Every connection
//   must have string `id`, `source`, `target`.
//   Empty nodes/connections arrays pass (`[].every` === true).

describe("validateEditorValue", () => {
    // ── Happy path ────────────────────────────────────────────────────────────
    it("accepts a well-formed editor value", () => {
        const value = {
            nodes: [{ id: "n1", data: { label: "A" } }],
            connections: [{ id: "c1", source: "n1", target: "n2" }],
        };
        expect(validateEditorValue(value)).toBe(true);
    });

    it("accepts empty nodes and connections arrays", () => {
        expect(validateEditorValue({ nodes: [], connections: [] })).toBe(true);
    });

    // ── Invalid input: non-objects ────────────────────────────────────────────
    it("rejects null", () => {
        expect(validateEditorValue(null)).toBe(false);
    });

    it("rejects undefined", () => {
        expect(validateEditorValue(undefined)).toBe(false);
    });

    it("rejects a primitive string", () => {
        expect(validateEditorValue("nope")).toBe(false);
    });

    it("rejects a number", () => {
        expect(validateEditorValue(42)).toBe(false);
    });

    // ── Invalid shape ─────────────────────────────────────────────────────────
    it("rejects when nodes is not an array", () => {
        expect(validateEditorValue({ nodes: {}, connections: [] })).toBe(false);
    });

    it("rejects when connections is not an array", () => {
        expect(validateEditorValue({ nodes: [], connections: {} })).toBe(false);
    });

    it("rejects when connections key is missing", () => {
        expect(validateEditorValue({ nodes: [] })).toBe(false);
    });

    // ── Invalid node contents ─────────────────────────────────────────────────
    it("rejects a node with a non-string id", () => {
        const value = {
            nodes: [{ id: 123, data: {} }],
            connections: [],
        };
        expect(validateEditorValue(value)).toBe(false);
    });

    it("rejects a node missing the data object (typeof undefined !== object)", () => {
        const value = {
            nodes: [{ id: "n1" }],
            connections: [],
        };
        expect(validateEditorValue(value)).toBe(false);
    });

    // ── Edge: typeof null === 'object' quirk (grounded in source) ─────────────
    it("accepts a node whose data is null (typeof null === 'object')", () => {
        // SOURCE: check is `typeof node.data === "object"`; null satisfies it.
        const value = {
            nodes: [{ id: "n1", data: null }],
            connections: [],
        };
        expect(validateEditorValue(value)).toBe(true);
    });

    // ── Invalid connection contents ───────────────────────────────────────────
    it("rejects a connection missing source", () => {
        const value = {
            nodes: [],
            connections: [{ id: "c1", target: "n2" }],
        };
        expect(validateEditorValue(value)).toBe(false);
    });

    it("rejects a connection with a non-string target", () => {
        const value = {
            nodes: [],
            connections: [{ id: "c1", source: "n1", target: 5 }],
        };
        expect(validateEditorValue(value)).toBe(false);
    });

    // ── Boundary: null entries inside arrays ─────────────────────────────────
    it("rejects when a node entry is null", () => {
        const value = { nodes: [null], connections: [] };
        expect(validateEditorValue(value)).toBe(false);
    });

    it("rejects when a connection entry is null", () => {
        const value = { nodes: [], connections: [null] };
        expect(validateEditorValue(value)).toBe(false);
    });
});
