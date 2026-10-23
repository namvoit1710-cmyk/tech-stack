import { describe, expect, it } from "@rstest/core";

import { findNearestPoint, isInsideRect } from "./math";
import type { Position, Rect } from "./types";

// SOURCE: math.ts
//   findNearestPoint: reduce over points; distance = sqrt((px-tx)^2+(py-ty)^2);
//     `if (distance > maxDistance) return nearestPoint` (point AT maxDistance is kept);
//     nearest kept with strict `<` (ties keep the earlier point);
//     returns `undefined` when accumulator stays null (`?.point`).
//   isInsideRect: strict inequalities with margin —
//     y > top-margin && x > left-margin && x < right+margin && y < bottom+margin.

describe("findNearestPoint", () => {
    // ── Happy path ──────────────────────────────────────────────────────────
    it("returns the closest point within maxDistance", () => {
        const points: Position[] = [
            { x: 0, y: 0 },
            { x: 10, y: 0 },
            { x: 3, y: 4 }, // distance 5 from origin
        ];
        const result = findNearestPoint(points, { x: 0, y: 0 }, 100);
        expect(result).toEqual({ x: 0, y: 0 });
    });

    it("selects nearest among several candidates", () => {
        const points: Position[] = [
            { x: 100, y: 100 },
            { x: 2, y: 0 }, // distance 2
            { x: 50, y: 50 },
        ];
        const result = findNearestPoint(points, { x: 0, y: 0 }, 1000);
        expect(result).toEqual({ x: 2, y: 0 });
    });

    it("preserves extra properties on the returned point (generic T)", () => {
        const points = [{ x: 1, y: 1, id: "a" }];
        const result = findNearestPoint(points, { x: 0, y: 0 }, 100);
        expect(result).toEqual({ x: 1, y: 1, id: "a" });
    });

    // ── Boundary ────────────────────────────────────────────────────────────
    it("keeps a point exactly at maxDistance (comparison is strict `>`)", () => {
        const points: Position[] = [{ x: 3, y: 4 }]; // distance 5
        const result = findNearestPoint(points, { x: 0, y: 0 }, 5);
        expect(result).toEqual({ x: 3, y: 4 });
    });

    it("rejects a point just beyond maxDistance", () => {
        const points: Position[] = [{ x: 3, y: 4 }]; // distance 5
        const result = findNearestPoint(points, { x: 0, y: 0 }, 4.999);
        expect(result).toBeUndefined();
    });

    it("keeps the earlier point when two are equidistant (strict `<` tie-break)", () => {
        const points: Position[] = [
            { x: 5, y: 0 }, // distance 5, first
            { x: 0, y: 5 }, // distance 5, second — not strictly less, so ignored
        ];
        const result = findNearestPoint(points, { x: 0, y: 0 }, 100);
        expect(result).toEqual({ x: 5, y: 0 });
    });

    // ── Edge ────────────────────────────────────────────────────────────────
    it("returns undefined for an empty points array", () => {
        const result = findNearestPoint([], { x: 0, y: 0 }, 100);
        expect(result).toBeUndefined();
    });

    it("handles negative coordinates", () => {
        const points: Position[] = [{ x: -3, y: -4 }]; // distance 5 from origin
        const result = findNearestPoint(points, { x: 0, y: 0 }, 10);
        expect(result).toEqual({ x: -3, y: -4 });
    });

    it("returns undefined when all points exceed maxDistance", () => {
        const points: Position[] = [
            { x: 100, y: 100 },
            { x: 200, y: 0 },
        ];
        const result = findNearestPoint(points, { x: 0, y: 0 }, 10);
        expect(result).toBeUndefined();
    });

    // ── Boundary: zero maxDistance ────────────────────────────────────────────
    it("with maxDistance 0 keeps only a point coincident with the target", () => {
        const points: Position[] = [{ x: 0, y: 0 }];
        const result = findNearestPoint(points, { x: 0, y: 0 }, 0);
        expect(result).toEqual({ x: 0, y: 0 });
    });

    it("with maxDistance 0 rejects any non-coincident point", () => {
        const points: Position[] = [{ x: 1, y: 0 }];
        const result = findNearestPoint(points, { x: 0, y: 0 }, 0);
        expect(result).toBeUndefined();
    });
});

describe("isInsideRect", () => {
    const rect: Rect = { left: 0, top: 0, right: 100, bottom: 100 };

    // ── Happy path ────────────────────────────────────────────────────────────
    it("returns true for a point clearly inside", () => {
        expect(isInsideRect(rect, { x: 50, y: 50 }, 0)).toBe(true);
    });

    // ── Invalid / outside ───────────────────────────────────────────────────
    it("returns false for a point outside on the right", () => {
        expect(isInsideRect(rect, { x: 150, y: 50 }, 0)).toBe(false);
    });

    it("returns false for a point outside on the left", () => {
        expect(isInsideRect(rect, { x: -10, y: 50 }, 0)).toBe(false);
    });

    it("returns false for a point above the top", () => {
        expect(isInsideRect(rect, { x: 50, y: -10 }, 0)).toBe(false);
    });

    it("returns false for a point below the bottom", () => {
        expect(isInsideRect(rect, { x: 50, y: 150 }, 0)).toBe(false);
    });

    // ── Boundary: strict inequalities ─────────────────────────────────────────
    it("returns false for a point exactly on the left edge (strict `>`)", () => {
        // x > left-margin => 0 > 0 is false
        expect(isInsideRect(rect, { x: 0, y: 50 }, 0)).toBe(false);
    });

    it("returns false for a point exactly on the bottom edge (strict `<`)", () => {
        // y < bottom+margin => 100 < 100 is false
        expect(isInsideRect(rect, { x: 50, y: 100 }, 0)).toBe(false);
    });

    // ── Edge: margin widens the acceptance region ─────────────────────────────
    it("accepts a point just outside when margin covers the gap", () => {
        // x=105 > right+margin? 105 < 100+10 => inside
        expect(isInsideRect(rect, { x: 105, y: 50 }, 10)).toBe(true);
    });

    it("still rejects a point exactly on the margin-extended edge (strict)", () => {
        // x < right+margin => 110 < 110 is false
        expect(isInsideRect(rect, { x: 110, y: 50 }, 10)).toBe(false);
    });

    it("negative margin shrinks the acceptance region", () => {
        // margin -10: x > left+10 (=> x>10) && x < right-10 (=> x<90)
        expect(isInsideRect(rect, { x: 50, y: 50 }, -10)).toBe(true);
        expect(isInsideRect(rect, { x: 5, y: 50 }, -10)).toBe(false);
    });

    // ── Boundary: degenerate (zero-area) rect ────────────────────────────────
    it("a zero-area rect with margin 0 contains no points (all strict)", () => {
        const degenerate: Rect = { left: 5, top: 5, right: 5, bottom: 5 };
        expect(isInsideRect(degenerate, { x: 5, y: 5 }, 0)).toBe(false);
    });
});
