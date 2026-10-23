import { describe, expect, it } from "@rstest/core";
import { resolveTableCellDisplayValue } from "./resolve-cell-display";

describe("resolveTableCellDisplayValue", () => {
    // happy — resolves a scalar value to its option label (valueKey=id, labelKey=value defaults)
    it("resolves a scalar value to the matching option label", () => {
        const result = resolveTableCellDisplayValue({
            value: "1",
            options: [
                { id: "1", value: "Active" },
                { id: "2", value: "Inactive" },
            ],
            shouldResolveOptions: true,
        });
        expect(result).toBe("Active");
    });

    // happy — resolves an array of values to labels
    it("resolves an array of values to an array of labels", () => {
        const result = resolveTableCellDisplayValue({
            value: ["1", "2"],
            options: [
                { id: "1", value: "A" },
                { id: "2", value: "B" },
            ],
            shouldResolveOptions: true,
        });
        expect(result).toEqual(["A", "B"]);
    });

    // edge / FINDING (A1-FE-1) — no scalar match yields `undefined`, NOT the raw value.
    // Source truth: resolveOptionLabel calls toDisplayPrimitive(found?.[labelKey], value);
    // found is undefined so candidate is undefined, and toDisplayPrimitive returns a null/undefined
    // candidate verbatim (its `fallback` arg is only used for non-primitive object candidates).
    // The raw value ("99") is therefore lost. This test pins the ACTUAL behavior.
    it("returns undefined for a scalar when no option matches (raw value is dropped)", () => {
        const result = resolveTableCellDisplayValue({
            value: "99",
            options: [{ id: "1", value: "A" }],
            shouldResolveOptions: true,
        });
        expect(result).toBeUndefined();
    });

    // edge — array no-match: each unmatched entry likewise becomes undefined
    it("maps unmatched array entries to undefined", () => {
        const result = resolveTableCellDisplayValue({
            value: ["1", "99"],
            options: [{ id: "1", value: "A" }],
            shouldResolveOptions: true,
        });
        expect(result).toEqual(["A", undefined]);
    });

    // edge — empty options short-circuits to the raw value (still formatted)
    it("returns the value unchanged when options is empty", () => {
        expect(
            resolveTableCellDisplayValue({ value: "raw", options: [], shouldResolveOptions: true }),
        ).toBe("raw");
    });

    // edge — shouldResolveOptions=false skips label resolution but still formats
    it("skips option resolution when shouldResolveOptions is false", () => {
        const result = resolveTableCellDisplayValue({
            value: "1",
            options: [{ id: "1", value: "A" }],
            shouldResolveOptions: false,
        });
        expect(result).toBe("1");
    });

    // happy — custom valueKey / labelKey
    it("honors custom valueKey and labelKey", () => {
        const result = resolveTableCellDisplayValue({
            value: "us",
            options: [{ code: "us", name: "United States" }],
            valueKey: "code",
            labelKey: "name",
            shouldResolveOptions: true,
        });
        expect(result).toBe("United States");
    });

    // edge — string/number primitive options are normalized to {label,value}
    it("normalizes primitive (string/number) options", () => {
        const result = resolveTableCellDisplayValue({
            value: "hello",
            options: ["hello", "world"],
            valueKey: "value",
            labelKey: "label",
            shouldResolveOptions: true,
        });
        expect(result).toBe("hello");
    });

    // happy — formats a number with the Intl number formatter
    it("formats a numeric value with grouping", () => {
        const result = resolveTableCellDisplayValue({
            value: 1234567,
            shouldResolveOptions: false,
        });
        expect(result).toBe("1,234,567");
    });

    // happy — formats an ISO date-time string (UTC, en-US medium/short)
    it("formats an ISO date-time string in UTC", () => {
        const result = resolveTableCellDisplayValue({
            value: "2026-07-02T13:45:00Z",
            shouldResolveOptions: false,
        });
        // en-US, dateStyle medium + timeStyle short, timeZone UTC
        expect(result).toBe("Jul 2, 2026, 1:45 PM");
    });

    // edge — a non-ISO string is left as-is
    it("leaves a non-ISO string unchanged", () => {
        expect(
            resolveTableCellDisplayValue({ value: "not a date", shouldResolveOptions: false }),
        ).toBe("not a date");
    });

    // invalid input — an ISO-shaped but invalid date is returned unchanged
    it("returns an ISO-shaped-but-invalid date string unchanged", () => {
        const value = "2026-13-45T99:99:99Z"; // matches pattern, invalid date
        expect(resolveTableCellDisplayValue({ value, shouldResolveOptions: false })).toBe(value);
    });

    // boundary — null / undefined values pass through
    it("passes through null and undefined values", () => {
        expect(resolveTableCellDisplayValue({ value: null, shouldResolveOptions: false })).toBeNull();
        expect(
            resolveTableCellDisplayValue({ value: undefined, shouldResolveOptions: false }),
        ).toBeUndefined();
    });

    // edge — boolean value passes through unformatted
    it("passes a boolean value through unchanged", () => {
        expect(resolveTableCellDisplayValue({ value: true, shouldResolveOptions: false })).toBe(true);
    });

    // edge — array of numbers is each formatted
    it("formats each numeric entry of an array value", () => {
        expect(
            resolveTableCellDisplayValue({ value: [1000, 2000], shouldResolveOptions: false }),
        ).toEqual(["1,000", "2,000"]);
    });

    // edge — option label falls back through name/text/value/id chain
    it("derives an option label from name/text when label is absent", () => {
        const result = resolveTableCellDisplayValue({
            value: "1",
            options: [{ id: "1", name: "Named" }],
            valueKey: "id",
            labelKey: "label",
            shouldResolveOptions: true,
        });
        // normalizeOptions sets label from name; resolveOptionLabel returns option[labelKey]="label"=Named
        expect(result).toBe("Named");
    });
});
