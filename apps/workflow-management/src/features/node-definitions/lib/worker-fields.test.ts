import { describe, expect, it } from "@rstest/core";
import {
    DEFAULT_WORKER_TYPE,
    HTTP_WORKER_FIELDS,
    HTTP_WORKER_OUTPUT_FIELDS,
    HTTP_WORKER_TYPE,
    getWorkerFieldMeta,
    getWorkerFields,
    getWorkerOutputFields,
    type IWorkerInputSchemaField,
    resolveWorkerFields,
    workerFieldsFromSchema,
} from "./worker-fields";

describe("getWorkerFields", () => {
    it("returns HTTP fields for the service-name worker type (happy)", () => {
        expect(getWorkerFields(DEFAULT_WORKER_TYPE)).toBe(HTTP_WORKER_FIELDS);
    });

    it("returns HTTP fields for the bare http_request worker type (happy)", () => {
        expect(getWorkerFields(HTTP_WORKER_TYPE)).toBe(HTTP_WORKER_FIELDS);
    });

    it("falls back to HTTP fields for an unknown worker type (edge)", () => {
        expect(getWorkerFields("some-unknown-worker")).toBe(HTTP_WORKER_FIELDS);
    });

    it("falls back to HTTP fields when worker type is undefined (invalid)", () => {
        expect(getWorkerFields(undefined)).toBe(HTTP_WORKER_FIELDS);
    });
});

describe("getWorkerOutputFields", () => {
    it("returns HTTP output fields for a known type (happy)", () => {
        expect(getWorkerOutputFields(HTTP_WORKER_TYPE)).toBe(HTTP_WORKER_OUTPUT_FIELDS);
    });

    it("falls back to HTTP output fields for unknown/undefined (edge/invalid)", () => {
        expect(getWorkerOutputFields("nope")).toBe(HTTP_WORKER_OUTPUT_FIELDS);
        expect(getWorkerOutputFields(undefined)).toBe(HTTP_WORKER_OUTPUT_FIELDS);
    });
});

describe("getWorkerFieldMeta", () => {
    it("finds a field by key (happy)", () => {
        expect(getWorkerFieldMeta("method")?.label).toBe("HTTP Method");
    });

    it("returns a key_value_array field with its column keys (happy)", () => {
        const headers = getWorkerFieldMeta("headers");
        expect(headers?.mode).toBe("key_value_array");
        expect(headers?.nameKey).toBe("header_name");
        expect(headers?.valueKey).toBe("header_value");
    });

    it("returns undefined for an unknown key (invalid)", () => {
        expect(getWorkerFieldMeta("does_not_exist")).toBeUndefined();
    });
});

describe("workerFieldsFromSchema", () => {
    it("maps a schema field, preferring wrapperProps.label (happy)", () => {
        const schema: IWorkerInputSchemaField[] = [
            { key: "amount", outputType: "number", fieldConfig: { wrapperProps: { label: "Amount" }, controlProps: { placeholder: "Enter amount" } } },
        ];
        expect(workerFieldsFromSchema(schema)).toEqual([
            { key: "amount", label: "Amount", description: "Enter amount", mode: "expression" },
        ]);
    });

    it("uses json_template mode for object/array output types (boundary)", () => {
        const schema: IWorkerInputSchemaField[] = [
            { key: "payload", outputType: "object", fieldConfig: {} },
            { key: "items", outputType: "array", fieldConfig: {} },
        ];
        const out = workerFieldsFromSchema(schema);
        expect(out.map((f) => f.mode)).toEqual(["json_template", "json_template"]);
    });

    it("falls back label→fieldConfig.label→key and description to empty (edge)", () => {
        expect(workerFieldsFromSchema([{ key: "k", fieldConfig: { label: "L" } }])).toEqual([
            { key: "k", label: "L", description: "", mode: "expression" },
        ]);
        expect(workerFieldsFromSchema([{ key: "bare" }])).toEqual([
            { key: "bare", label: "bare", description: "", mode: "expression" },
        ]);
    });

    it("filters out fields with no key (invalid)", () => {
        expect(workerFieldsFromSchema([{ key: "" }, { key: "ok" }])).toEqual([
            { key: "ok", label: "ok", description: "", mode: "expression" },
        ]);
    });

    it("returns [] for empty/undefined/null schema (edge)", () => {
        expect(workerFieldsFromSchema([])).toEqual([]);
        expect(workerFieldsFromSchema(undefined)).toEqual([]);
        expect(workerFieldsFromSchema(null)).toEqual([]);
    });
});

describe("resolveWorkerFields", () => {
    it("curated registry entry wins over schema (happy)", () => {
        const schema: IWorkerInputSchemaField[] = [{ key: "x" }];
        expect(resolveWorkerFields(HTTP_WORKER_TYPE, schema)).toBe(HTTP_WORKER_FIELDS);
    });

    it("derives from schema for an unknown worker type (happy)", () => {
        const schema: IWorkerInputSchemaField[] = [{ key: "custom", outputType: "string" }];
        expect(resolveWorkerFields("custom-worker", schema)).toEqual([
            { key: "custom", label: "custom", description: "", mode: "expression" },
        ]);
    });

    it("falls back to HTTP fields for unknown worker with no schema (edge)", () => {
        expect(resolveWorkerFields("unknown", null)).toBe(HTTP_WORKER_FIELDS);
        expect(resolveWorkerFields(undefined, undefined)).toBe(HTTP_WORKER_FIELDS);
    });
});
