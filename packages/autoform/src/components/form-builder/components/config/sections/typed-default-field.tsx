import { DatePicker } from "@ldc/ui/blocks/date-picker/date-picker";
import { DateTimePicker } from "@ldc/ui/blocks/date-time-picker/date-time-picker";
import { TimePicker } from "@ldc/ui/blocks/time-picker/time-picker";
import { Checkbox } from "@ldc/ui/components/checkbox";
import { Input } from "@ldc/ui/components/input";
import { Label } from "@ldc/ui/components/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@ldc/ui/components/select";
import { Switch } from "@ldc/ui/components/switch";
import { useId } from "react";
import type { ISelectOption } from "../../../../field-control/select-control";

const BOOLEAN_CONTROLS = new Set(["CheckBoxControl", "SwitchControl"]);
const NONE = "__none__";

/** Date/Time controls whose Default value is a picker (single, or a {from,to} range) — SA-2038 R3. */
const DATE_TIME_DEFAULT: Record<string, "date" | "datetime" | "time"> = {
    DatePickerControl: "date",
    DateTimePickerControl: "datetime",
    TimePickerControl: "time",
};

export interface TypedDefaultFieldProps {
    controlKey: string;
    controlProps: Record<string, unknown>;
    value: unknown;
    onChange: (next: unknown) => void;
}

/**
 * The Identity "Default value", typed to the control (SA-2036 R3):
 *  - Checkbox/Switch → a real boolean Switch.
 *  - Select/Radio (static) → a picker over the current options (a typed id that is not an
 *    option selects nothing, so never offer free text).
 *  - Combobox/SelectTable with `multiple` → a multi-pick (array default).
 *  - serverOptions source → the option list is server-fetched, so fall back to a free-text
 *    id + a note.
 */
export function TypedDefaultField({ controlKey, controlProps, value, onChange }: TypedDefaultFieldProps) {
    const uid = useId();

    // Date/Time controls (SA-2038 R3): the default is a picker matching the control; range mode
    // (Date, DateTime) uses the {from,to} range picker — its outputType is object.
    const dtType = DATE_TIME_DEFAULT[controlKey];
    if (dtType) {
        const isRange = controlProps.mode === "range";
        const asRange = value as { from?: string; to?: string } | undefined;
        const asStr = typeof value === "string" ? value : undefined;
        if (dtType === "date") {
            return isRange ? (
                <DatePicker mode="range" value={asRange} onChange={(v) => onChange(v)} placeholder="Default range" />
            ) : (
                <DatePicker mode="single" value={asStr} onChange={(v) => onChange(v)} placeholder="Default date" />
            );
        }
        if (dtType === "datetime") {
            return isRange ? (
                <DateTimePicker mode="range" value={asRange} onChange={(v) => onChange(v)} placeholder="Default range" />
            ) : (
                <DateTimePicker value={asStr} onChange={(v) => onChange(v)} placeholder="Default date-time" />
            );
        }
        return <TimePicker value={asStr} onChange={(v) => onChange(v)} placeholder="Default time" />;
    }

    if (BOOLEAN_CONTROLS.has(controlKey)) {
        const checked = value === true || value === "true";
        return (
            <div className="flex items-center gap-2">
                <Switch checked={checked} onCheckedChange={(c: boolean) => onChange(c)} />
            </div>
        );
    }

    const options = Array.isArray(controlProps.options) ? (controlProps.options as ISelectOption[]) : [];
    const hasServer = !!controlProps.serverOptions;
    const multiple = controlProps.multiple === true;

    if (hasServer) {
        return (
            <div className="flex flex-col gap-1">
                <Input
                    className="h-8! text-xs!"
                    placeholder="option id"
                    value={typeof value === "string" ? value : ""}
                    onChange={(e) => onChange(e.target.value)}
                />
                <p className="text-[10px] text-muted-foreground">
                    Options are loaded from the server; type an option id.
                </p>
            </div>
        );
    }

    if (multiple) {
        const selected = Array.isArray(value) ? (value as string[]) : [];
        const toggle = (id: string) =>
            onChange(selected.includes(id) ? selected.filter((v) => v !== id) : [...selected, id]);
        return (
            <div className="flex flex-col gap-1">
                {options.map((opt) => {
                    const id = `${uid}-${opt.id}`;
                    return (
                        <div key={opt.id} className="flex items-center gap-2">
                            <Checkbox id={id} checked={selected.includes(opt.id)} onCheckedChange={() => toggle(opt.id)} />
                            <Label htmlFor={id} className="text-xs font-normal cursor-pointer">
                                {opt.value}
                            </Label>
                        </div>
                    );
                })}
            </div>
        );
    }

    const current = typeof value === "string" && value !== "" ? value : NONE;
    return (
        <Select value={current} onValueChange={(v: string) => onChange(v === NONE ? "" : v)}>
            <SelectTrigger className="h-8! text-xs! w-full">
                <SelectValue placeholder="(pick an option)" />
            </SelectTrigger>
            <SelectContent>
                <SelectItem value={NONE} className="text-xs!">
                    (none)
                </SelectItem>
                {options.map((opt) => (
                    <SelectItem key={opt.id} value={opt.id} className="text-xs!">
                        {opt.value}
                    </SelectItem>
                ))}
            </SelectContent>
        </Select>
    );
}

export default TypedDefaultField;
