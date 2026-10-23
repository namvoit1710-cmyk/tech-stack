import { describe, expect, it } from "@rstest/core";

import { createPort, generatePortId } from "./port-utils";

// SOURCE: port-utils.ts
//   generatePortId(nodeId, type) => `${nodeId}_${type}`  (plain template string).
//   createPort(id, label?, config?) =>
//     { id, label: label || id, readonly: config?.readonly,
//       required: config?.required, ...config }
//   NOTE: the spread `...config` is applied LAST, so keys `readonly`/`required`
//     inside config OVERRIDE the explicitly-picked ones.

describe("generatePortId", () => {
    // ── Happy path ────────────────────────────────────────────────────────────
    it("joins nodeId and 'input' with an underscore", () => {
        expect(generatePortId("node1", "input")).toBe("node1_input");
    });

    it("joins nodeId and 'output' with an underscore", () => {
        expect(generatePortId("node1", "output")).toBe("node1_output");
    });

    // ── Edge ────────────────────────────────────────────────────────────────
    it("handles an empty nodeId", () => {
        expect(generatePortId("", "input")).toBe("_input");
    });

    it("preserves underscores already present in the nodeId", () => {
        expect(generatePortId("a_b_c", "output")).toBe("a_b_c_output");
    });

    it("preserves a UUID-shaped nodeId verbatim", () => {
        const id = "550e8400-e29b-41d4-a716-446655440000";
        expect(generatePortId(id, "input")).toBe(`${id}_input`);
    });
});

describe("createPort", () => {
    // ── Happy path ────────────────────────────────────────────────────────────
    it("builds a port with id and explicit label", () => {
        const port = createPort("p1", "Port One");
        expect(port).toEqual({
            id: "p1",
            label: "Port One",
            readonly: undefined,
            required: undefined,
        });
    });

    // ── Edge: label defaulting ────────────────────────────────────────────────
    it("defaults label to id when label is omitted", () => {
        const port = createPort("p1");
        expect(port.label).toBe("p1");
    });

    it("defaults label to id when label is an empty string (falsy `||`)", () => {
        const port = createPort("p1", "");
        expect(port.label).toBe("p1");
    });

    // ── Config merge ──────────────────────────────────────────────────────────
    it("copies readonly and required from config", () => {
        const port = createPort("p1", "L", { readonly: true, required: false });
        expect(port.readonly).toBe(true);
        expect(port.required).toBe(false);
    });

    it("spreads extra config keys onto the port", () => {
        const port = createPort("p1", "L", { color: "red" });
        expect(port).toMatchObject({ id: "p1", label: "L", color: "red" });
    });

    it("config keys override the explicitly-picked readonly/required (spread is last)", () => {
        // SOURCE: readonly/required are set, then `...config` spread overwrites.
        // Here config only defines readonly, so the picked `readonly: undefined`
        // is overwritten with true; required stays the picked undefined.
        const port = createPort("p1", "L", { readonly: true } as {
            readonly?: boolean;
            required?: boolean;
        });
        expect(port.readonly).toBe(true);
        expect(port.required).toBeUndefined();
    });

    // ── Edge: empty config object ─────────────────────────────────────────────
    it("leaves readonly/required undefined for an empty config object", () => {
        const port = createPort("p1", "L", {});
        expect(port.readonly).toBeUndefined();
        expect(port.required).toBeUndefined();
    });
});
