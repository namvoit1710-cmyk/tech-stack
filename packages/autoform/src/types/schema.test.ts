import { describe, expect, it } from "@rstest/core";
import type { IField, IFieldConfig } from "./schema";

describe("IFieldConfig.script", () => {
    it("accepts an event-name -> source map co-located on the field", () => {
        const field: IField = {
            key: "qty",
            outputType: "number",
            fieldConfig: {
                fieldControl: "InputControl",
                script: {
                    onChange:
                        "async function onChange(ctx) { ctx.setValue('total', Number(ctx.event.value)); }",
                },
            },
        };
        const cfg: IFieldConfig = field.fieldConfig;
        expect(cfg.script?.onChange).toContain("ctx.setValue");
        expect(typeof cfg.script).toBe("object");
    });
});
