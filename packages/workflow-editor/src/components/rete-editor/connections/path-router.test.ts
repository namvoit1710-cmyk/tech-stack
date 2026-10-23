import { describe, expect, it } from "@rstest/core";

import { computeStepPath } from "./path-router";

// SOURCE: path-router.ts
//   R = 12, SOCKET_OFFSET = 12.
//   computeStepPath(source, target, direction="horizontal") dispatches to
//     computeVerticalStepPath (direction === "vertical") else computeHorizontalStepPath.
//
//   Horizontal branches:
//     A) dx > 40 && |dy| > 5 -> rounded S path, angle 0, r = min(12,|dx|/2,|dy|/2).
//     B) dx <= 0 && dy > 0    -> loop path (uses source.x+50), angle 0.
//     C) dx <= 0 && dy < 0    -> loop path, angle 0.
//     D) else                 -> straight `M sx sy L tx ty`, angle = dx>=0 ? 0 : 180.
//
//   Vertical (operates on sx=source.x-12, tx=target.x+12):
//     A) dy > 40 && |dx| > 5  -> rounded path, angle 90.
//     B) dy > 40 && |dx| <= 5 -> straight, angle 90.
//     C) dy <= 0 && dx >= 0   -> loop path, angle 90.
//     D) dy <= 0 && dx < 0    -> loop path, angle 90.
//     E) else                 -> straight `M sx sy L tx ty`, angle = dy>=0 ? 90 : 270.

const START = (x: number, y: number) => new RegExp(`^M ${x} ${y}\\b`);
const END = (x: number, y: number) => new RegExp(`L ${x} ${y}$`);

describe("computeStepPath — horizontal (default)", () => {
    // ── Branch A: rounded S path ──────────────────────────────────────────────
    it("returns a rounded path with angle 0 when dx>40 and |dy|>5", () => {
        const res = computeStepPath({ x: 0, y: 0 }, { x: 100, y: 50 });
        expect(res.angle).toBe(0);
        expect(res.d).toContain("Q"); // rounded => quadratic segments present
        expect(res.d).toMatch(START(0, 0));
        expect(res.d).toMatch(END(100, 50));
    });

    it("clamps corner radius r to min(12,|dx|/2,|dy|/2) for a tight dy", () => {
        // dy=6 (>5), dx=100 (>40): r = min(12, 50, 3) = 3 -> first L is xMid-3.
        // xMid = (0+100)/2 = 50 -> `L 47 0`
        const res = computeStepPath({ x: 0, y: 0 }, { x: 100, y: 6 });
        expect(res.d).toContain("L 47 0");
    });

    // ── Branch D: straight fallback, forward ──────────────────────────────────
    it("returns a straight forward line (angle 0) when dy is within +/-5", () => {
        const res = computeStepPath({ x: 0, y: 0 }, { x: 100, y: 3 });
        expect(res).toEqual({ d: "M 0 0 L 100 3", angle: 0 });
    });

    it("uses the straight fallback when dx is small (<=40) and dy tiny", () => {
        const res = computeStepPath({ x: 0, y: 0 }, { x: 30, y: 0 });
        expect(res).toEqual({ d: "M 0 0 L 30 0", angle: 0 });
    });

    // ── Branch D: straight fallback, backward => angle 180 ────────────────────
    it("sets angle 180 for a backward straight line (dx<0, |dy|<=5)", () => {
        // dx=-30 (<=0) but dy=0 (not >0, not <0) so branches B/C skip -> fallback D
        const res = computeStepPath({ x: 100, y: 0 }, { x: 70, y: 0 });
        expect(res).toEqual({ d: "M 100 0 L 70 0", angle: 180 });
    });

    // ── Branch B: dx<=0, dy>0 loop ────────────────────────────────────────────
    it("returns a loop path (angle 0) when dx<=0 and dy>0", () => {
        const res = computeStepPath({ x: 100, y: 0 }, { x: 0, y: 50 });
        expect(res.angle).toBe(0);
        expect(res.d).toContain("Q");
        expect(res.d).toMatch(START(100, 0));
        expect(res.d).toMatch(END(0, 50));
    });

    // ── Branch C: dx<=0, dy<0 loop ────────────────────────────────────────────
    it("returns a loop path (angle 0) when dx<=0 and dy<0", () => {
        const res = computeStepPath({ x: 100, y: 100 }, { x: 0, y: 0 });
        expect(res.angle).toBe(0);
        expect(res.d).toContain("Q");
        expect(res.d).toMatch(START(100, 100));
        expect(res.d).toMatch(END(0, 0));
    });

    // ── Boundary: dx exactly 40 (not > 40) falls through to fallback ──────────
    it("treats dx exactly 40 as not > 40 (branch A excluded)", () => {
        // dx=40 not >40; dy=50>0 but dx(40) not <=0 -> fallback D, angle 0
        const res = computeStepPath({ x: 0, y: 0 }, { x: 40, y: 50 });
        expect(res).toEqual({ d: "M 0 0 L 40 50", angle: 0 });
    });

    // ── Edge: identical points (degenerate) ───────────────────────────────────
    it("returns a zero-length straight line for coincident points", () => {
        const res = computeStepPath({ x: 10, y: 10 }, { x: 10, y: 10 });
        expect(res).toEqual({ d: "M 10 10 L 10 10", angle: 0 });
    });

    // ── Edge: negative coordinates still valid ────────────────────────────────
    it("handles negative source/target coordinates", () => {
        const res = computeStepPath({ x: -100, y: -50 }, { x: 0, y: 0 });
        // dx=100>40, dy=50>5 -> rounded path, angle 0
        expect(res.angle).toBe(0);
        expect(res.d).toMatch(START(-100, -50));
        expect(res.d).toMatch(END(0, 0));
    });
});

describe("computeStepPath — vertical", () => {
    // Vertical shifts endpoints: sx = source.x - 12, tx = target.x + 12.

    // ── Branch A: rounded, angle 90 ───────────────────────────────────────────
    it("returns a rounded path with angle 90 when dy>40 and |dx|>5", () => {
        // source(0,0) target(50,100): sx=-12, tx=62 -> dx=74>5, dy=100>40
        const res = computeStepPath({ x: 0, y: 0 }, { x: 50, y: 100 }, "vertical");
        expect(res.angle).toBe(90);
        expect(res.d).toContain("Q");
        expect(res.d).toMatch(START(-12, 0)); // sx applied
        expect(res.d).toMatch(END(62, 100)); // tx applied
    });

    // ── Branch B: straight vertical, angle 90 ─────────────────────────────────
    it("returns a straight vertical line (angle 90) when dy>40 and |dx| small", () => {
        // source(0,0) target(0,100): sx=-12, tx=12 -> dx=24 ... NOT <=5.
        // Need |dx|<=5 AFTER offsets: choose target.x so tx-sx in [-5,5].
        // sx = source.x-12, tx = target.x+12; want tx-sx<=5 => target.x-source.x<=-19.
        // source(20,0), target(0,100): sx=8, tx=12, dx=4 (<=5), dy=100>40 -> straight.
        const res = computeStepPath({ x: 20, y: 0 }, { x: 0, y: 100 }, "vertical");
        expect(res).toEqual({ d: "M 8 0 L 12 100", angle: 90 });
    });

    // ── Branch C: dy<=0, dx>=0 loop ───────────────────────────────────────────
    it("returns a loop path (angle 90) when dy<=0 and dx>=0", () => {
        // source(0,100) target(50,0): sx=-12, tx=62 -> dx=74>=0, dy=-100<=0
        const res = computeStepPath({ x: 0, y: 100 }, { x: 50, y: 0 }, "vertical");
        expect(res.angle).toBe(90);
        expect(res.d).toContain("Q");
        expect(res.d).toMatch(START(-12, 100));
        expect(res.d).toMatch(END(62, 0));
    });

    // ── Branch D: dy<=0, dx<0 loop ────────────────────────────────────────────
    it("returns a loop path (angle 90) when dy<=0 and dx<0", () => {
        // source(100,100) target(0,0): sx=88, tx=12 -> dx=-76<0, dy=-100<=0
        const res = computeStepPath({ x: 100, y: 100 }, { x: 0, y: 0 }, "vertical");
        expect(res.angle).toBe(90);
        expect(res.d).toContain("Q");
        expect(res.d).toMatch(START(88, 100));
        expect(res.d).toMatch(END(12, 0));
    });

    // ── Branch E: fallback angle by dy sign ───────────────────────────────────
    it("fallback uses angle 90 when none of the shaped branches match with dy>=0", () => {
        // Want to fall through: dy in (0,40], |dx|>5. source(0,0) target(0,20):
        // sx=-12, tx=12, dx=24, dy=20 -> not (dy>40), not (dy<=0) -> fallback E, dy>=0 -> 90
        const res = computeStepPath({ x: 0, y: 0 }, { x: 0, y: 20 }, "vertical");
        expect(res).toEqual({ d: "M -12 0 L 12 20", angle: 90 });
    });
});
