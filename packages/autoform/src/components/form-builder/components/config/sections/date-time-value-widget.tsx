import { DatePicker } from "@ldc/ui/blocks/date-picker/date-picker";
import { DateTimePicker } from "@ldc/ui/blocks/date-time-picker/date-time-picker";
import { TimePicker } from "@ldc/ui/blocks/time-picker/time-picker";

import { Help, type WidgetProps } from "./prop-widgets";

/**
 * `date` | `datetime` | `time` — bounds & default value widget (SA-2038). Reuses the @ldc/ui
 * pickers; a single component branches on `prop.type` because the three PropTypes share the
 * same scalar-string value shape (ISO date, UTC datetime, or HH:MM:SS) and the same "clearing
 * deletes the key" commit rule.
 *
 * Split out of prop-widgets.tsx (rather than added there) to keep that file under the
 * config-folder 300-line size guard (`config-panel.size.test.ts`) — prop-widgets.tsx was
 * already at 293 lines.
 */
export function DateTimeValueWidget({ prop, current, id, label, set, unset }: WidgetProps) {
    // Bounds/default are single scalar strings (ISO date, UTC datetime, or HH:MM:SS).
    const strVal = typeof current === "string" ? current : undefined;
    // Clearing the picker (undefined/empty) DELETES the key — absence is a real state,
    // exactly like the enum "Not set" and the emptied number input.
    const commit = (v: string | undefined) =>
        v === undefined || v === "" ? unset(prop.key) : set(prop.key, v);
    return (
        <div className="flex flex-col gap-1">
            {/* The pickers own their own trigger/focusable, which no htmlFor reaches, AND
                already surface `label` as their placeholder text when empty — so the group
                takes its accessible name from aria-label directly rather than a separate
                caption element, which would duplicate that same visible text. */}
            <div id={id} role="group" aria-label={label}>
                {prop.type === "date" && (
                    <DatePicker mode="single" value={strVal} onChange={commit} placeholder={label} />
                )}
                {prop.type === "datetime" && (
                    <DateTimePicker value={strVal} onChange={commit} placeholder={label} />
                )}
                {prop.type === "time" && (
                    <TimePicker value={strVal} onChange={commit} placeholder={label} />
                )}
            </div>
            <Help text={prop.help} />
        </div>
    );
}
