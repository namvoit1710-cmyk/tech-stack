import { describe, expect, it } from "@rstest/core";
import type { IEnvironment } from "@/features/environments/types/environment";
import { filledCount, NONE_CRED, optimisticBadKeyIndexes } from "./section-schema";

const env = (variables: { key: string; value: string }[]): IEnvironment => ({
    id: "e1",
    tenant_id: "t1",
    section: "MCK",
    name: "dev",
    is_active: true,
    version: 1,
    created_at: "",
    updated_at: "",
    variables: variables.map(v => ({ key: v.key, type: "text", value: v.value, values: [] })),
});

describe("filledCount", () => {
    it("counts only non-empty, non-sentinel values for the section's keys", () => {
        const e = env([
            { key: "URL", value: "https://x" },
            { key: "TOKEN", value: "" },
            { key: "OAUTH", value: NONE_CRED },
        ]);
        const keys = [
            { key: "URL", type: "text" as const },
            { key: "TOKEN", type: "text" as const },
            { key: "OAUTH", type: "credential" as const },
        ];
        expect(filledCount(e, keys)).toBe(1);
    });

    it("treats a key missing from the env's variables as empty", () => {
        const e = env([{ key: "URL", value: "https://x" }]);
        const keys = [
            { key: "URL", type: "text" as const },
            { key: "MISSING", type: "text" as const },
        ];
        expect(filledCount(e, keys)).toBe(1);
    });
});

describe("optimisticBadKeyIndexes", () => {
    it("returns an empty set when keys are unique and non-empty", () => {
        expect(optimisticBadKeyIndexes([{ key: "A" }, { key: "B" }]).size).toBe(0);
    });

    it("flags every index of a case-insensitive duplicate", () => {
        const bad = optimisticBadKeyIndexes([{ key: "OAUTH" }, { key: "url" }, { key: "oauth" }]);
        expect([...bad].sort()).toEqual([0, 2]);
    });

    it("flags empty / whitespace-only keys", () => {
        const bad = optimisticBadKeyIndexes([{ key: "A" }, { key: "" }, { key: "   " }]);
        expect([...bad].sort()).toEqual([1, 2]);
    });
});
