import { describe, expect, it } from "@rstest/core";
import { createEmptyField, type ISchemaFieldItem } from "./converters";
import {
    customFieldSuggestions,
    inputExpressionSuggestions,
    inputHintSuggestion,
    workerOutputSchemaSuggestions,
    workerOutputSuggestions,
} from "./expression-suggestions";

const fields: ISchemaFieldItem[] = [
    { ...createEmptyField(), key: "query", label: "Search Query", outputType: "string" },
    { ...createEmptyField(), key: "limit", label: "", outputType: "number" },
    { ...createEmptyField(), key: "", label: "no key", outputType: "string" }, // dropped (no key)
];

describe("expression-suggestions", () => {
    it("maps custom fields to {{$custom.<key>}} with humanized type + group", () => {
        const s = customFieldSuggestions(fields);
        expect(s).toHaveLength(2);
        expect(s[0]).toMatchObject({ value: "{{$custom.query}}", label: "Search Query", description: "Text", group: "Custom inputs" });
        expect(s[1]).toMatchObject({ value: "{{$custom.limit}}", label: "limit", description: "Number" });
    });

    it("builds {{$_output.*}} worker-output suggestions (default http)", () => {
        const s = workerOutputSuggestions();
        expect(s.find((x) => x.value === "{{$_output.status_code}}")).toBeTruthy();
        expect(s.find((x) => x.value === "{{$_output.response_body}}")).toBeTruthy();
        expect(s.every((x) => x.group === "Worker output")).toBe(true);
    });

    it("appends the $input hint to input-expression suggestions", () => {
        const s = inputExpressionSuggestions(fields);
        expect(s).toHaveLength(3); // 2 custom + 1 hint
        expect(s.at(-1)).toMatchObject({ value: "{{$input.", group: "Workflow input" });
    });

    it("builds {{$_output.*}} from the base worker's response schema (label from fieldConfig)", () => {
        const schema = [
            { key: "status_code", outputType: "number", fieldConfig: { wrapperProps: { label: "Status Code" } } },
            { key: "data", outputType: "object", fieldConfig: {} },
            { key: "", outputType: "string" }, // dropped (no key)
        ];
        const s = workerOutputSchemaSuggestions(schema);
        expect(s).toHaveLength(2);
        expect(s[0]).toMatchObject({ value: "{{$_output.status_code}}", label: "Status Code", description: "Number", group: "Worker output" });
        expect(s[1]!.label).toBe("data"); // falls back to key when no label
        expect(workerOutputSchemaSuggestions(undefined)).toEqual([]); // empty → caller uses static fallback
    });

    it("allows overriding group labels (i18n)", () => {
        expect(customFieldSuggestions(fields, "Entrées")[0]!.group).toBe("Entrées");
        expect(inputHintSuggestion("WF", "desc")).toMatchObject({ group: "WF", description: "desc" });
    });
});
