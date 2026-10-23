import { describe, expect, it } from "@rstest/core";
import { formatShortDate, formatVersionLabel, VERSION_LABEL_LENGTH } from "./version-utils";

describe("formatVersionLabel", () => {
    it("truncates a long version to VERSION_LABEL_LENGTH chars (happy)", () => {
        expect(formatVersionLabel("abcdef1234567890")).toBe("abcdef12");
        expect(VERSION_LABEL_LENGTH).toBe(8);
    });

    it("returns a short version unchanged (edge)", () => {
        expect(formatVersionLabel("abc")).toBe("abc");
    });

    it("returns exactly the string when length equals the limit (boundary)", () => {
        expect(formatVersionLabel("12345678")).toBe("12345678");
        expect(formatVersionLabel("123456789")).toBe("12345678");
    });

    it("returns the em-dash placeholder for null/undefined/empty (invalid)", () => {
        expect(formatVersionLabel(null)).toBe("—");
        expect(formatVersionLabel(undefined)).toBe("—");
        expect(formatVersionLabel("")).toBe("—");
    });
});

describe("formatShortDate", () => {
    it("formats a valid ISO date string into a non-empty label (happy)", () => {
        const out = formatShortDate("2026-07-02T13:45:00Z");
        expect(typeof out).toBe("string");
        expect(out.length).toBeGreaterThan(0);
    });

    it("throws RangeError for an unparseable date string (failure)", () => {
        // FINDING (§A2->robustness): source is unguarded — Intl.DateTimeFormat.format(new Date("bad"))
        // throws "Invalid time value" rather than returning a placeholder. Asserting ACTUAL behavior.
        expect(() => formatShortDate("not-a-date")).toThrow(/Invalid time value/);
    });
});
