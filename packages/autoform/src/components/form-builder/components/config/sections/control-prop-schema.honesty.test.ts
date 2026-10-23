import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "@rstest/core";
import { CONTROL_PROP_SCHEMA } from "../../../constants/prop-keys";

/**
 * Native <input>/<textarea> attributes that ride `{...rest}` straight onto the underlying
 * element. A schema key the component does NOT read by name is only honest if it is one of
 * these — anything else would be a prop the control silently ignores.
 */
const NATIVE_PASSTHROUGH_ALLOWLIST = new Set(["placeholder", "maxLength", "minLength", "readOnly", "className"]);

/** Where each control's implementation lives, relative to the field-control root. A control may
 * map to MORE than one file when it forwards keys via `{...rest}` into a child component. */
const CONTROL_SOURCE: Record<string, string | string[]> = {
    InputControl: "input-control.tsx",
    TextareaControl: "textarea-control.tsx",
    PasswordControl: "password-control/password-control.tsx",
    NumberControl: "number-control.tsx",
    // SA-2036 Choice controls (Tier 4). Registry aliases per field-control/index.ts:
    // RadioPopupControl -> RadioControl (radio-control.tsx).
    CheckBoxControl: "checkbox-control.tsx",
    SwitchControl: "switch-control.tsx",
    SelectControl: "select-control.tsx",
    ComboboxControl: "combobox-control/combobox-control.tsx",
    RadioPopupControl: "radio-control.tsx",
    SelectTableControl: "select-table-control/select-table-control.tsx",
    // SA-2038 Date/Time controls (Tier 4). Each reads its own schema keys
    // (mode/timeMode/min*/max*); placeholder rides the passthrough allowlist.
    DatePickerControl: "date-picker-control.tsx",
    DateTimePickerControl: "date-time-picker-control.tsx",
    TimePickerControl: "time-picker-control.tsx",
    // SA-2040 Advanced controls. Code/Button read every key themselves; UploadControl forwards
    // accept/maxSize/multiple via `{...rest}` into the @ldc/ui <Upload>, so that component's
    // source counts too (accept/maxSize are read there) — hence the multi-file mapping.
    CodeControl: "code-control.tsx",
    UploadControl: ["upload-control.tsx", "../../../../ui/src/components/ui/upload.tsx"],
    ButtonControl: "button-control.tsx",
};

const FIELD_CONTROL_DIR = join(__dirname, "../../../../field-control");

function readSource(control: string): string {
    const rel = CONTROL_SOURCE[control];
    if (!rel) throw new Error(`No source file mapped for "${control}". Add it to CONTROL_SOURCE.`);
    const files = Array.isArray(rel) ? rel : [rel];
    return files.map((f) => readFileSync(join(FIELD_CONTROL_DIR, f), "utf8")).join("\n");
}

/** The component names the key as an identifier — a real, explicit read (Number's min/max/readonly). */
function isExplicitlyRead(source: string, key: string): boolean {
    return new RegExp(`\\b${key}\\b`).test(source);
}

/**
 * A hardcoded string-literal attribute — `className="pr-10"` — NEUTRALISES whatever rode through
 * `{...rest}`: the prop is a lie. A merge — `className={cn("pr-10", className)}` — re-references
 * the key, so it is honoured. This is exactly the PasswordControl clobber the `cn` fix removed.
 */
function isClobbered(source: string, key: string): boolean {
    // A JSX attribute literal — `className="pr-10"` — has NO space before `=` (JSX convention). A
    // destructuring DEFAULT — `placeholder = "Write your code here…"` in CodeControl — has a space
    // before `=` and is NOT a clobber (the prop still flows through when supplied). Require the JSX
    // no-space form so a default parameter is not mistaken for a hardcoded attribute.
    const literal = new RegExp(`${key}=\\s*["'][^"']*["']`);
    if (!literal.test(source)) return false;
    const merge = new RegExp(`${key}=\\s*\\{[^}]*\\b${key}\\b[^}]*\\}`);
    return !merge.test(source);
}

describe("CONTROL_PROP_SCHEMA honesty — every offered prop is actually honoured", () => {
    for (const [control, schema] of Object.entries(CONTROL_PROP_SCHEMA)) {
        for (const prop of schema) {
            it(`${control}.${prop.key} is read by the component or an allowlisted passthrough`, () => {
                const source = readSource(control);
                const honoured = isExplicitlyRead(source, prop.key) || NATIVE_PASSTHROUGH_ALLOWLIST.has(prop.key);
                expect(honoured).toBe(true);
            });

            // The clobber check only means anything for a NATIVE PASSTHROUGH prop — one that
            // rides `{...rest}` onto the element, where a hardcoded literal (`className="pr-10"`)
            // silently neutralises it. A component-specific prop that is explicitly READ and
            // branched — e.g. the Date/DateTime wrappers pass `mode="single" | "range"` literals
            // chosen from the resolved `mode` (the discriminated-union value types force a literal
            // per branch) — is NOT a passthrough, so a literal there is the control's own value
            // choice, not a clobber. Gating on the allowlist keeps the real protection intact
            // (every passthrough key is still clobber-checked) while dropping that false positive.
            if (NATIVE_PASSTHROUGH_ALLOWLIST.has(prop.key)) {
                it(`${control}.${prop.key} is not neutralised by a hardcoded literal`, () => {
                    const source = readSource(control);
                    expect(isClobbered(source, prop.key)).toBe(false);
                });
            }
        }
    }
});
