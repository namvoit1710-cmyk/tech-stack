import { describe, expect, it } from "@rstest/core";
import { collectActions } from "./collect-action";
import type { IField, ISchema } from "../../../types/schema";
import type { ActionConfig } from "../types";

const action = (name: string): ActionConfig => ({ action: name, steps: [] });

const field = (partial: Partial<IField> & Pick<IField, "key" | "outputType">): IField => ({
    fieldConfig: {},
    ...partial,
});

describe("collectActions", () => {
    // edge — undefined / empty schema returns []
    it("returns an empty array for undefined or empty schemas", () => {
        expect(collectActions(undefined)).toEqual([]);
        expect(collectActions({ fields: [] } as ISchema)).toEqual([]);
        expect(collectActions({} as ISchema)).toEqual([]);
    });

    // happy — collects wrapperProps.actions
    it("collects actions from fieldConfig.wrapperProps.actions", () => {
        const schema: ISchema = {
            fields: [
                field({
                    key: "a",
                    outputType: "string",
                    fieldConfig: { wrapperProps: { actions: [action("save"), action("refresh")] } },
                }),
            ],
        };
        const result = collectActions(schema);
        expect(result.map((a) => a.action).sort()).toEqual(["refresh", "save"]);
    });

    // happy — collects controlProps.actionConfig
    it("collects a single action from fieldConfig.controlProps.actionConfig", () => {
        const schema: ISchema = {
            fields: [
                field({
                    key: "a",
                    outputType: "string",
                    fieldConfig: { controlProps: { actionConfig: action("submit") } },
                }),
            ],
        };
        expect(collectActions(schema).map((a) => a.action)).toEqual(["submit"]);
    });

    // edge — dedupes by action name (Map keyed by action.action, last wins)
    it("dedupes actions by action name", () => {
        const first = { action: "dup", steps: [], label: "first" } as ActionConfig;
        const second = { action: "dup", steps: [], label: "second" } as ActionConfig;
        const schema: ISchema = {
            fields: [
                field({ key: "a", outputType: "string", fieldConfig: { wrapperProps: { actions: [first] } } }),
                field({ key: "b", outputType: "string", fieldConfig: { wrapperProps: { actions: [second] } } }),
            ],
        };
        const result = collectActions(schema);
        expect(result).toHaveLength(1);
        expect(result[0]?.label).toBe("second");
    });

    // happy — recurses into nested fields
    it("recurses into nested fields", () => {
        const schema: ISchema = {
            fields: [
                field({
                    key: "parent",
                    outputType: "object",
                    fields: [
                        field({
                            key: "child",
                            outputType: "string",
                            fieldConfig: { controlProps: { actionConfig: action("deep") } },
                        }),
                    ],
                }),
            ],
        };
        expect(collectActions(schema).map((a) => a.action)).toEqual(["deep"]);
    });

    // edge — fields with no actions contribute nothing
    it("ignores fields without any actions", () => {
        const schema: ISchema = {
            fields: [field({ key: "plain", outputType: "string" })],
        };
        expect(collectActions(schema)).toEqual([]);
    });

    // boundary — empty actions array yields nothing (guard on actions?.length)
    it("skips an empty actions array", () => {
        const schema: ISchema = {
            fields: [
                field({ key: "a", outputType: "string", fieldConfig: { wrapperProps: { actions: [] } } }),
            ],
        };
        expect(collectActions(schema)).toEqual([]);
    });
});
