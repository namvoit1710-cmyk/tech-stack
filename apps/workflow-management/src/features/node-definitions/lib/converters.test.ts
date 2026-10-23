import { describe, expect, it } from "@rstest/core";
import {
    autoFormToSchemaFields,
    schemaFieldsToAutoForm,
    schemaFieldsValid,
    GATEWAY_APPS_SERVER_OPTIONS,
    schemaFieldsToPreviewFields,
    type ISchemaFieldItem,
    entriesToLockedDefaults,
    lockedDefaultsToEntries,
    workerSchemaToEntries,
    type ILockedDefaultEntry,
    mappingEntriesToObject,
    objectToMappingEntries,
    keyValuePairsToObject,
    objectToKeyValuePairs,
    createEmptyField,
} from "./converters";

describe("converters", () => {
    describe("input schema (ISchemaFieldItem ↔ AutoFormField)", () => {
        const fields: ISchemaFieldItem[] = [
            { ...createEmptyField(), key: "query", label: "Search Query", outputType: "string", fieldControl: "InputControl", required: true, placeholder: "type…" },
            { ...createEmptyField(), key: "mode", label: "Mode", outputType: "string", fieldControl: "SelectControl", options: "Fast, Slow" },
        ];

        it("round-trips field key/label/type/required/placeholder", () => {
            const round = autoFormToSchemaFields(schemaFieldsToAutoForm(fields));
            expect(round[0]).toMatchObject({ key: "query", label: "Search Query", outputType: "string", required: true, placeholder: "type…" });
        });

        it("preserves dropdown options across the round-trip", () => {
            const round = autoFormToSchemaFields(schemaFieldsToAutoForm(fields));
            expect(round[1]?.options).toBe("Fast, Slow");
        });

        it("drops fields without a key", () => {
            expect(schemaFieldsToAutoForm([createEmptyField()])).toHaveLength(0);
        });

        it("routes array/object fields through CodeControl(json) so the backend keeps them", () => {
            const out = schemaFieldsToAutoForm([
                { ...createEmptyField(), key: "array_1", label: "Array 1", outputType: "array" },
                { ...createEmptyField(), key: "cfg", label: "Config", outputType: "object" },
            ]) as any[];
            expect(out[0].fieldConfig.fieldControl).toBe("CodeControl");
            expect(out[0].fieldConfig.controlProps.language).toBe("json");
            expect(out[1].fieldConfig.fieldControl).toBe("CodeControl");
            expect(out[1].fieldConfig.controlProps.language).toBe("json");
        });

        it("does not override an explicit option control on an array field", () => {
            const out = schemaFieldsToAutoForm([
                { ...createEmptyField(), key: "tags", outputType: "array", fieldControl: "MultipleComboboxControl", options: "A, B" },
            ]) as any[];
            expect(out[0].fieldConfig.fieldControl).toBe("MultipleComboboxControl");
        });

        it("round-trips array fields: type preserved, control falls back to a selectable one", () => {
            const round = autoFormToSchemaFields(
                schemaFieldsToAutoForm([{ ...createEmptyField(), key: "array_1", label: "Array 1", outputType: "array" }]),
            );
            expect(round[0]).toMatchObject({ key: "array_1", label: "Array 1", outputType: "array" });
            // CodeControl isn't offered in the field editor → dropdown shows the default.
            expect(round[0]?.fieldControl).toBe("InputControl");
        });

        it("handles empty/undefined input", () => {
            expect(autoFormToSchemaFields(undefined)).toEqual([]);
            expect(autoFormToSchemaFields([])).toEqual([]);
        });

        it("emits serverOptions (with valueKey/labelKey) and round-trips it", () => {
            const field: ISchemaFieldItem = {
                ...createEmptyField(),
                key: "app",
                fieldControl: "SelectControl",
                serverOptions: GATEWAY_APPS_SERVER_OPTIONS,
            };
            const af: any = schemaFieldsToAutoForm([field])[0];
            const cp = af.fieldConfig.controlProps;
            expect(cp.serverOptions).toMatchObject({ service: "workflow", endpoint: "gateway/apps" });
            // SelectControl reads valueKey/labelKey off raw items — must be set or labels render blank.
            expect(cp.valueKey).toBe("name");
            expect(cp.labelKey).toBe("name");
            expect(cp.options).toBeUndefined(); // server-driven options replace the static list

            const round = autoFormToSchemaFields([af])[0];
            expect(round?.serverOptions).toMatchObject({ service: "workflow", endpoint: "gateway/apps", valueField: "name", labelField: "name" });
        });

        it("splits dependencies into an array and back into a comma string", () => {
            const field: ISchemaFieldItem = {
                ...createEmptyField(),
                key: "fn",
                fieldControl: "ComboBoxControl",
                serverOptions: { ...GATEWAY_APPS_SERVER_OPTIONS, dependencies: "app, region" },
            };
            const af: any = schemaFieldsToAutoForm([field])[0];
            expect(af.fieldConfig.controlProps.serverOptions.dependencies).toEqual(["app", "region"]);
            expect(autoFormToSchemaFields([af])[0]?.serverOptions?.dependencies).toBe("app, region");
        });

        it("does not emit a half-configured serverOptions, and flags it invalid", () => {
            const half: ISchemaFieldItem = {
                ...createEmptyField(),
                key: "x",
                fieldControl: "SelectControl",
                serverOptions: { service: "", endpoint: "", valueField: "id", labelField: "value", dependencies: "" },
            };
            expect(schemaFieldsValid([half])).toBe(false);
            // incomplete → never written into the saved schema
            const cp: any = (schemaFieldsToAutoForm([half])[0] as any).fieldConfig.controlProps;
            expect(cp.serverOptions).toBeUndefined();
        });

        it("treats fields without serverOptions as valid", () => {
            expect(schemaFieldsValid([{ ...createEmptyField(), key: "a" }])).toBe(true);
        });
    });

    describe("preview fields (Inputs live-preview & Input-Mapping 'Try it')", () => {
        it("renders array/object fields as a JSON leaf (CodeControl + string type), not a repeater", () => {
            const out = schemaFieldsToPreviewFields([
                { ...createEmptyField(), key: "array_1", label: "Array 1", outputType: "array", fieldControl: "InputControl" },
                { ...createEmptyField(), key: "cfg", label: "Config", outputType: "object", fieldControl: "InputControl" },
            ]) as any[];
            // outputType collapsed to string so the Builder shows the leaf control...
            expect(out[0].outputType).toBe("string");
            expect(out[1].outputType).toBe("string");
            // ...and that control is the JSON code editor.
            expect(out[0].fieldConfig.fieldControl).toBe("CodeControl");
            expect(out[0].fieldConfig.controlProps.language).toBe("json");
        });

        it("lays fields out full-width for the narrow panels", () => {
            const [field] = schemaFieldsToPreviewFields([
                { ...createEmptyField(), key: "query", label: "Query", outputType: "string" },
            ]) as any[];
            expect(field.fieldConfig.wrapperProps.fieldSpan).toBe("xl-12 lg-12 md-12 xs-12");
            expect(field.fieldConfig.controlProps.className).toBe("w-full");
        });

        it("leaves scalar fields and their control untouched", () => {
            const [field] = schemaFieldsToPreviewFields([
                { ...createEmptyField(), key: "mode", label: "Mode", outputType: "string", fieldControl: "SelectControl", options: "Fast, Slow" },
            ]) as any[];
            expect(field.outputType).toBe("string");
            expect(field.fieldConfig.fieldControl).toBe("SelectControl");
            expect(field.fieldConfig.controlProps.options).toEqual([
                { id: "fast", value: "Fast" },
                { id: "slow", value: "Slow" },
            ]);
        });
    });

    describe("locked defaults (entries ↔ dict)", () => {
        it("only emits locked, non-empty entries with typed coercion", () => {
            const entries: ILockedDefaultEntry[] = [
                { key: "method", label: "Method", value: "GET", type: "string", locked: true },
                { key: "timeout_seconds", label: "Timeout", value: "30", type: "number", locked: true },
                { key: "verify_ssl", label: "Verify", value: "true", type: "boolean", locked: true },
                { key: "ignored", label: "Ignored", value: "x", type: "string", locked: false },
                { key: "empty", label: "Empty", value: "", type: "string", locked: true },
            ];
            expect(entriesToLockedDefaults(entries)).toEqual({ method: "GET", timeout_seconds: 30, verify_ssl: true });
        });

        it("round-trips a plain dict (no worker schema)", () => {
            const obj = { method: "POST", timeout_seconds: 60, verify_ssl: false };
            expect(entriesToLockedDefaults(lockedDefaultsToEntries(obj))).toEqual(obj);
        });

        it("persists a code field once it has content (the Defaults-tab save→reload round-trip)", () => {
            // Repro of the "code empty after reload" bug: the field starts
            // unlocked+empty. Typing content auto-locks it (defaults-section
            // renderCodeEditor), so it must now survive save → reload.
            const schema = [
                {
                    key: "code",
                    outputType: "string",
                    fieldConfig: { fieldControl: "CodeControl", controlProps: { language: "python" } },
                },
            ] as any;

            // Fresh load: no saved defaults → unlocked, empty, not persisted.
            const [fresh] = lockedDefaultsToEntries(undefined, schema);
            expect(fresh.locked).toBe(false);
            expect(entriesToLockedDefaults([fresh])).toEqual({});

            // User typed a script → auto-locked → now saved.
            const typed = { ...fresh, value: "return items", locked: true };
            const saved = entriesToLockedDefaults([typed]);
            expect(saved).toEqual({ code: "return items" });

            // Reload from saved defaults → comes back locked with the script.
            const [reloaded] = lockedDefaultsToEntries(saved, schema);
            expect(reloaded.value).toBe("return items");
            expect(reloaded.locked).toBe(true);
        });

        it("carries controlType + controlProps so code fields render their editor", () => {
            const schema = [
                {
                    key: "code",
                    outputType: "string",
                    fieldConfig: {
                        fieldControl: "CodeControl",
                        controlProps: { language: "javascript", languageField: "language", placeholder: "return items;" },
                    },
                },
            ] as any;
            const [entry] = workerSchemaToEntries(schema, { code: "return 1;" });
            expect(entry.controlType).toBe("CodeControl");
            expect(entry.controlProps).toEqual({ language: "javascript", languageField: "language", placeholder: "return items;" });
            expect(entry.value).toBe("return 1;");
            expect(entry.locked).toBe(true);
        });
    });

    describe("input mapping (entries ↔ dict, http worker)", () => {
        it("round-trips expression + key_value_array + json_template fields", () => {
            const obj = {
                url: "https://api.example.com",
                query_params: [{ param_name: "q", param_value: "{{$custom.query}}" }],
                body: '{"x": "{{$custom.x}}"}',
            };
            expect(mappingEntriesToObject(objectToMappingEntries(obj))).toEqual(obj);
        });

        it("ignores keys that are not worker fields", () => {
            expect(objectToMappingEntries({ not_a_worker_field: "v" })).toEqual([]);
        });

        it("keeps keys recognized by a dynamic (non-HTTP) workerFields list", () => {
            // e.g. a code worker — `array_1` is not an HTTP field, so it would be
            // dropped by the default filter, but the resolved field list keeps it.
            const workerFields = [{ key: "array_1", label: "Array 1", description: "", mode: "expression" as const }];
            const entries = objectToMappingEntries({ array_1: "{{$custom.array_1}}" }, undefined, workerFields);
            expect(entries).toHaveLength(1);
            expect(entries[0]).toMatchObject({ workerField: "array_1", expressionValue: "{{$custom.array_1}}" });
        });

        it("round-trips through a dynamic workerFields list", () => {
            const workerFields = [{ key: "array_1", label: "Array 1", description: "", mode: "expression" as const }];
            const obj = { array_1: "{{$custom.array_1}}" };
            expect(mappingEntriesToObject(objectToMappingEntries(obj, undefined, workerFields), undefined, workerFields)).toEqual(obj);
        });
    });

    describe("output mapping (pairs ↔ dict)", () => {
        it("round-trips string values", () => {
            const obj = { result: "{{$_output.response_body}}", code: "{{$_output.status_code}}" };
            expect(keyValuePairsToObject(objectToKeyValuePairs(obj))).toEqual(obj);
        });

        it("drops pairs without a key", () => {
            expect(keyValuePairsToObject([{ key: "", value: "x" }])).toEqual({});
        });
    });
});
