import { describe, expect, it } from "@rstest/core";

import { createEditorConnection, isValidConnection } from "./create-connection";

// SOURCE: create-connection.ts
//   createEditorConnection(params) =>
//     { id: params.id || `connection_${params.source}_${params.target}`,
//       source, sourceOutput, target, targetInput }
//   isValidConnection(source, target, nodeIds: Set) =>
//     nodeIds.has(source) && nodeIds.has(target)

describe("createEditorConnection", () => {
    // ── Happy path ────────────────────────────────────────────────────────────
    it("builds a connection with all provided fields", () => {
        const conn = createEditorConnection({
            id: "c1",
            source: "s",
            sourceOutput: "out",
            target: "t",
            targetInput: "in",
        });
        expect(conn).toEqual({
            id: "c1",
            source: "s",
            sourceOutput: "out",
            target: "t",
            targetInput: "in",
        });
    });

    // ── Edge: id defaulting ───────────────────────────────────────────────────
    it("derives a default id from source and target when id omitted", () => {
        const conn = createEditorConnection({
            source: "nodeA",
            sourceOutput: "out",
            target: "nodeB",
            targetInput: "in",
        });
        expect(conn.id).toBe("connection_nodeA_nodeB");
    });

    it("derives a default id when id is an empty string (falsy `||`)", () => {
        const conn = createEditorConnection({
            id: "",
            source: "a",
            sourceOutput: "o",
            target: "b",
            targetInput: "i",
        });
        expect(conn.id).toBe("connection_a_b");
    });

    // ── Edge: self connection ─────────────────────────────────────────────────
    it("allows source and target to be the same node id", () => {
        const conn = createEditorConnection({
            source: "x",
            sourceOutput: "o",
            target: "x",
            targetInput: "i",
        });
        expect(conn.id).toBe("connection_x_x");
    });
});

describe("isValidConnection", () => {
    // ── Happy path ────────────────────────────────────────────────────────────
    it("returns true when both source and target exist in the set", () => {
        const ids = new Set(["a", "b"]);
        expect(isValidConnection("a", "b", ids)).toBe(true);
    });

    // ── Failure paths ─────────────────────────────────────────────────────────
    it("returns false when source is missing", () => {
        const ids = new Set(["b"]);
        expect(isValidConnection("a", "b", ids)).toBe(false);
    });

    it("returns false when target is missing", () => {
        const ids = new Set(["a"]);
        expect(isValidConnection("a", "b", ids)).toBe(false);
    });

    it("returns false when both are missing", () => {
        const ids = new Set(["x"]);
        expect(isValidConnection("a", "b", ids)).toBe(false);
    });

    // ── Edge ──────────────────────────────────────────────────────────────────
    it("returns false against an empty set", () => {
        expect(isValidConnection("a", "b", new Set())).toBe(false);
    });

    it("returns true for a self-connection when the node exists", () => {
        expect(isValidConnection("a", "a", new Set(["a"]))).toBe(true);
    });
});
