import { describe, expect, it } from "@rstest/core";
import { buildColumnFilterString } from "./use-odata-toolbar";

describe("buildColumnFilterString", () => {
    it("returns undefined for empty filters", () => {
        expect(buildColumnFilterString({})).toBeUndefined();
    });

    it("returns undefined when all filter values are empty", () => {
        expect(buildColumnFilterString({ name: { operator: "is", values: [] } })).toBeUndefined();
    });

    it("is - single value", () => {
        expect(buildColumnFilterString({ status: { operator: "is", values: ["active"] } }))
            .toBe("status eq 'active'");
    });

    it("is - multiple values joined with or", () => {
        expect(buildColumnFilterString({ status: { operator: "is", values: ["a", "b"] } }))
            .toBe("(status eq 'a' or status eq 'b')");
    });

    it("is_not - single value", () => {
        expect(buildColumnFilterString({ status: { operator: "is_not", values: ["deleted"] } }))
            .toBe("status ne 'deleted'");
    });

    it("is_not - multiple values joined with and", () => {
        expect(buildColumnFilterString({ status: { operator: "is_not", values: ["a", "b"] } }))
            .toBe("(status ne 'a' and status ne 'b')");
    });

    it("contains - single value", () => {
        expect(buildColumnFilterString({ name: { operator: "contains", values: ["foo"] } }))
            .toBe("contains(name,'foo')");
    });

    it("contains - multiple values joined with or", () => {
        expect(buildColumnFilterString({ name: { operator: "contains", values: ["foo", "bar"] } }))
            .toBe("(contains(name,'foo') or contains(name,'bar'))");
    });

    it("not_contains - single value", () => {
        expect(buildColumnFilterString({ name: { operator: "not_contains", values: ["foo"] } }))
            .toBe("not contains(name,'foo')");
    });

    it("not_contains - multiple values joined with and", () => {
        expect(buildColumnFilterString({ name: { operator: "not_contains", values: ["foo", "bar"] } }))
            .toBe("(not contains(name,'foo') and not contains(name,'bar'))");
    });

    it("multiple columns joined with and", () => {
        const result = buildColumnFilterString({
            status: { operator: "is", values: ["active"] },
            name: { operator: "contains", values: ["foo"] },
        });
        expect(result).toBe("status eq 'active' and contains(name,'foo')");
    });

    it("escapes single quotes in values", () => {
        expect(buildColumnFilterString({ name: { operator: "is", values: ["o'brien"] } }))
            .toBe("name eq 'o''brien'");
    });
});
