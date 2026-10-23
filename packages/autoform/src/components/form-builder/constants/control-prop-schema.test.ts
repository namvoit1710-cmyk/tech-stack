import { describe, expect, it } from "@rstest/core";
import { resolveControlPropSchema } from "./prop-keys";

const keysOf = (control: string) => resolveControlPropSchema(control).map((p) => p.key);

describe("CONTROL_PROP_SCHEMA / resolveControlPropSchema", () => {
    it("returns [] for an unknown or undefined control", () => {
        expect(resolveControlPropSchema(undefined)).toEqual([]);
        expect(resolveControlPropSchema("NopeControl")).toEqual([]);
    });

    it("InputControl offers exactly placeholder/maxLength/minLength/readOnly/className", () => {
        expect(keysOf("InputControl")).toEqual(["placeholder", "maxLength", "minLength", "readOnly", "className"]);
    });

    it("TextareaControl offers the same five and never the dead `rows`", () => {
        expect(keysOf("TextareaControl")).toEqual(["placeholder", "maxLength", "minLength", "readOnly", "className"]);
        expect(keysOf("TextareaControl")).not.toContain("rows");
    });

    it("PasswordControl offers only placeholder/readOnly/className (length is a validation rule)", () => {
        expect(keysOf("PasswordControl")).toEqual(["placeholder", "readOnly", "className"]);
        expect(keysOf("PasswordControl")).not.toContain("maxLength");
    });

    it("NumberControl offers placeholder/min/max/readonly/className and never the dead `step`", () => {
        expect(keysOf("NumberControl")).toEqual(["placeholder", "min", "max", "readonly", "className"]);
        expect(keysOf("NumberControl")).not.toContain("step");
    });

    it("uses lowercase `readonly` for NumberControl but native `readOnly` for the others", () => {
        expect(resolveControlPropSchema("NumberControl").find((p) => p.label === "Read-only")?.key).toBe("readonly");
        expect(resolveControlPropSchema("InputControl").find((p) => p.label === "Read-only")?.key).toBe("readOnly");
    });

    it("never offers the rejected validation/niche props on Input", () => {
        for (const dead of ["type", "pattern", "autoComplete", "inputMode"]) {
            expect(keysOf("InputControl")).not.toContain(dead);
        }
    });

    it("types the numeric props as number, readOnly as boolean, placeholder as string", () => {
        const byKey = Object.fromEntries(resolveControlPropSchema("NumberControl").map((p) => [p.key, p.type]));
        expect(byKey.min).toBe("number");
        expect(byKey.max).toBe("number");
        expect(byKey.readonly).toBe("boolean");
        expect(byKey.placeholder).toBe("string");
    });
});
