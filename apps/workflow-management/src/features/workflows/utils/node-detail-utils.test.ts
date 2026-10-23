import { describe, expect, it } from "@rstest/core";
import type { INodeOutputScope } from "../types/execution";
import type { IScopedVariable } from "../types/node-detail";
import type {
    IDesignTimeSuggestionMatch,
    IDesignTimeSuggestionScopeSummary,
    IVariableSuggestionScope,
} from "../types/workflows";
import {
    normalizeDesignTimeMatchesToScopes,
    scopedVariablesToExprSuggestions,
    transformNodeOutputScopesToScopedVariables,
    transformVariableSuggestionScopesToScopedVariables,
} from "./node-detail-utils";

const outputScope = (over: Partial<INodeOutputScope>): INodeOutputScope => ({
    scope_type: "node",
    scope_id: "n1",
    label: "Node 1",
    expression_prefix: "{{$node1",
    paths: [],
    ...over,
});

const outputPath = (over: Partial<INodeOutputScope["paths"][number]>) => ({
    path: "field",
    kind: "scalar",
    output_type: "string",
    item_type: null,
    sample: "s",
    columns: [],
    preview_rows: [],
    label: "field",
    expandable: false,
    display_path: "$node1.field",
    ...over,
});

describe("transformNodeOutputScopesToScopedVariables", () => {
    it("maps snake_case scope fields to camelCase and folds paths into a record (happy)", () => {
        const result = transformNodeOutputScopesToScopedVariables([
            outputScope({
                paths: [outputPath({ label: "amount", display_path: "$n1.amount", output_type: "number" })],
                has_file_data: true,
                file_id: "f1",
                columns: ["a"],
            }),
        ]);
        expect(result).toHaveLength(1);
        expect(result[0].scopeType).toBe("node");
        expect(result[0].scopeId).toBe("n1");
        expect(result[0].expressionPrefix).toBe("{{$node1");
        expect(result[0].hasFileData).toBe(true);
        expect(result[0].fileId).toBe("f1");
        expect(result[0].columns).toEqual(["a"]);
        expect(result[0].paths.amount).toEqual({
            sample: "s",
            displayPath: "$n1.amount",
            kind: "scalar",
            outputType: "number",
        });
    });

    it("applies defaults when optional fields are absent (edge/boundary)", () => {
        const result = transformNodeOutputScopesToScopedVariables([
            outputScope({ paths: undefined as unknown as INodeOutputScope["paths"] }),
        ]);
        expect(result[0].paths).toEqual({});
        expect(result[0].hasFileData).toBe(false);
        expect(result[0].fileId).toBeNull();
        expect(result[0].columns).toEqual([]);
        expect(result[0].previewRows).toEqual([]);
        expect(result[0].artifacts).toEqual([]);
    });

    it("returns empty array for empty input (edge)", () => {
        expect(transformNodeOutputScopesToScopedVariables([])).toEqual([]);
    });

    it("later duplicate path labels overwrite earlier ones in the record (boundary)", () => {
        const result = transformNodeOutputScopesToScopedVariables([
            outputScope({
                paths: [
                    outputPath({ label: "dup", display_path: "first" }),
                    outputPath({ label: "dup", display_path: "second" }),
                ],
            }),
        ]);
        expect(result[0].paths.dup.displayPath).toBe("second");
    });
});

describe("normalizeDesignTimeMatchesToScopes", () => {
    const match = (over: Partial<IDesignTimeSuggestionMatch>): IDesignTimeSuggestionMatch => ({
        scope_type: "node",
        scope_id: "s1",
        scope_label: "Scope 1",
        node_type: "task",
        expression_prefix: "{{$s1",
        path: "p",
        kind: "scalar",
        output_type: "string",
        item_type: null,
        label: "p",
        display_path: "$s1.p",
        match_kind: "exact",
        ...over,
    });

    it("groups flat matches by scope_id, preserving first-seen order (happy)", () => {
        const result = normalizeDesignTimeMatchesToScopes(
            [
                match({ scope_id: "b", path: "p1" }),
                match({ scope_id: "a", path: "p2" }),
                match({ scope_id: "b", path: "p3" }),
            ],
            [],
        );
        expect(result.map((s) => s.scope_id)).toEqual(["b", "a"]);
        expect(result[0].paths).toHaveLength(2);
        expect(result[1].paths).toHaveLength(1);
    });

    it("prefers scope summary label/prefix over match values when a summary exists (boundary)", () => {
        const summary: IDesignTimeSuggestionScopeSummary = {
            scope_type: "node",
            scope_id: "s1",
            label: "Summary Label",
            expression_prefix: "{{$summary",
            match_count: 1,
            returned_count: 1,
            truncated: false,
        };
        const result = normalizeDesignTimeMatchesToScopes([match({ scope_id: "s1" })], [summary]);
        expect(result[0].label).toBe("Summary Label");
        expect(result[0].expression_prefix).toBe("{{$summary");
    });

    it("falls back to match label/prefix when no summary is provided (edge)", () => {
        const result = normalizeDesignTimeMatchesToScopes(
            [match({ scope_id: "s1", scope_label: "MatchLabel", expression_prefix: "{{$m" })],
            [],
        );
        expect(result[0].label).toBe("MatchLabel");
        expect(result[0].expression_prefix).toBe("{{$m");
    });

    it("returns empty array for no matches (edge)", () => {
        expect(normalizeDesignTimeMatchesToScopes([], [])).toEqual([]);
    });

    it("sets the fixed defaults on each normalized scope (invariant)", () => {
        const result = normalizeDesignTimeMatchesToScopes([match({ scope_id: "s1" })], []);
        expect(result[0].has_file_data).toBe(false);
        expect(result[0].file_id).toBeNull();
        expect(result[0].artifacts).toEqual([]);
        expect(result[0].default_artifact_path).toBeNull();
    });
});

describe("scopedVariablesToExprSuggestions", () => {
    const scoped = (paths: IScopedVariable["paths"]): IScopedVariable => ({
        label: "Node A",
        scopeType: "node",
        scopeId: "a",
        expressionPrefix: "{{$a",
        paths,
        artifacts: [],
        hasFileData: false,
        fileId: null,
        columns: [],
        previewRows: [],
    });

    it("flattens each path with a displayPath into a suggestion (happy)", () => {
        const result = scopedVariablesToExprSuggestions([
            scoped({
                amount: { sample: null, displayPath: "$a.amount", kind: "scalar", outputType: "number" },
            }),
        ]);
        expect(result).toEqual([
            { value: "$a.amount", label: "amount", description: "number", group: "Node A" },
        ]);
    });

    it("skips paths with a falsy displayPath (invalid/boundary)", () => {
        const result = scopedVariablesToExprSuggestions([
            scoped({
                good: { sample: null, displayPath: "$a.good", kind: "scalar", outputType: "string" },
                empty: { sample: null, displayPath: "", kind: "scalar", outputType: "string" },
            }),
        ]);
        expect(result).toHaveLength(1);
        expect(result[0].label).toBe("good");
    });

    it("returns empty for no scopes or empty paths (edge)", () => {
        expect(scopedVariablesToExprSuggestions([])).toEqual([]);
        expect(scopedVariablesToExprSuggestions([scoped({})])).toEqual([]);
    });
});

describe("transformVariableSuggestionScopesToScopedVariables", () => {
    const suggestionScope = (over: Partial<IVariableSuggestionScope>): IVariableSuggestionScope => ({
        scope_type: "run",
        scope_id: "r1",
        label: "Run",
        expression_prefix: "{{$run",
        paths: [],
        node_type: null,
        has_file_data: false,
        file_id: null,
        columns: [],
        preview_rows: [],
        artifacts: [],
        default_artifact_path: null,
        ...over,
    });

    it("maps a run-scoped suggestion scope into the camelCase scoped variable (happy)", () => {
        const result = transformVariableSuggestionScopesToScopedVariables([
            suggestionScope({
                paths: [
                    {
                        path: "x",
                        kind: "scalar",
                        output_type: "string",
                        item_type: null,
                        sample: "v",
                        columns: [],
                        preview_rows: [],
                        label: "x",
                        expandable: false,
                        display_path: "$run.x",
                    },
                ],
                has_file_data: true,
                file_id: "f9",
            }),
        ]);
        expect(result[0].scopeType).toBe("run");
        expect(result[0].hasFileData).toBe(true);
        expect(result[0].fileId).toBe("f9");
        expect(result[0].paths.x).toEqual({
            sample: "v",
            displayPath: "$run.x",
            kind: "scalar",
            outputType: "string",
        });
    });

    it("handles empty paths and empty input (edge)", () => {
        expect(transformVariableSuggestionScopesToScopedVariables([])).toEqual([]);
        const r = transformVariableSuggestionScopesToScopedVariables([suggestionScope({})]);
        expect(r[0].paths).toEqual({});
    });
});
