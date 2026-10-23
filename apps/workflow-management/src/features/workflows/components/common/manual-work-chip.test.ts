import { describe, expect, it } from "@rstest/core";
import { humanizeSeconds, WORK_DAY_SECONDS } from "./manual-work-chip";

// A "day" is a WORKING day (8h), because this humanizes saved human labour.
describe("humanizeSeconds (working-day)", () => {
    it("treats 8h of work as exactly one working day", () => {
        expect(WORK_DAY_SECONDS).toBe(28800);
        expect(humanizeSeconds(8 * 3600)).toBe("1d");
    });

    it("carries hours past a working day (was the 24h bug: 27h → 1d 3h)", () => {
        expect(humanizeSeconds(27 * 3600)).toBe("3d 3h"); // 3×8h + 3h
        expect(humanizeSeconds(40 * 3600)).toBe("5d");     // 5 working days
    });

    it("leaves sub-day durations unchanged", () => {
        expect(humanizeSeconds(6 * 3600)).toBe("6h");
        expect(humanizeSeconds(2 * 60 + 15)).toBe("2m 15s");
        expect(humanizeSeconds(45)).toBe("45s");
        expect(humanizeSeconds(0)).toBe("0s");
    });

    it("clamps negatives to 0", () => {
        expect(humanizeSeconds(-100)).toBe("0s");
    });
});
