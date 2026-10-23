import { describe, expect, it } from "@rstest/core";

import { generateUUID } from "./generate-uuid";

// SOURCE: generate-uuid.ts
//   Uses crypto.randomUUID() when available; otherwise a Math.random fallback
//   producing "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx" with version nibble 4 and
//   variant nibble in {8,9,a,b}.
const UUID_RE =
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

describe("generateUUID", () => {
    // ── Happy path ────────────────────────────────────────────────────────────
    it("returns a string in canonical v4 UUID shape", () => {
        expect(generateUUID()).toMatch(UUID_RE);
    });

    it("sets the version nibble to 4", () => {
        expect(generateUUID().charAt(14)).toBe("4");
    });

    it("sets the variant nibble to one of 8/9/a/b", () => {
        const variant = generateUUID().charAt(19).toLowerCase();
        expect(["8", "9", "a", "b"]).toContain(variant);
    });

    // ── Edge: uniqueness across many draws ────────────────────────────────────
    it("produces distinct values across repeated calls", () => {
        const set = new Set<string>();
        for (let i = 0; i < 1000; i++) set.add(generateUUID());
        expect(set.size).toBe(1000);
    });

    // ── Boundary: fixed length/format ─────────────────────────────────────────
    it("always has length 36", () => {
        expect(generateUUID()).toHaveLength(36);
    });
});
