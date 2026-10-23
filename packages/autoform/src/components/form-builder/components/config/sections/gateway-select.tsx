import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@ldc/ui/components/select";

/**
 * Radix's Select forbids an empty-string item value, but two of the Gateway pickers
 * need one: "nothing picked yet" and "do not send this arg". Both map to `""` in the
 * emitted config, so they go through a sentinel here and are translated back on change
 * — the same trick `prop-widgets.tsx` uses for its "Not set" entry.
 */
export const GATEWAY_SELECT_EMPTY = "__gateway_empty__";

export interface GatewaySelectOption {
    value: string;
    label: string;
}

export interface GatewaySelectProps {
    /** Announced name; the tests and the a11y tree both key off this. */
    ariaLabel: string;
    /** `""` renders as the placeholder entry (see GATEWAY_SELECT_EMPTY). */
    value: string;
    onChange: (next: string) => void;
    options: GatewaySelectOption[];
    /** Label for the `""` entry. Omit to leave the empty value unselectable. */
    emptyLabel?: string;
    disabled?: boolean;
    className?: string;
}

/**
 * The one Select every Gateway authoring picker uses — the design-system component from
 * `@ldc/ui`, not a raw `<select>`, so these panels match the rest of the config panel.
 */
export function GatewaySelect({
    ariaLabel,
    value,
    onChange,
    options,
    emptyLabel,
    disabled,
    className = "h-8! w-full text-xs!",
}: GatewaySelectProps) {
    return (
        <Select
            value={value === "" ? GATEWAY_SELECT_EMPTY : value}
            onValueChange={(v) => onChange(v === GATEWAY_SELECT_EMPTY ? "" : v)}
            disabled={disabled}
        >
            <SelectTrigger aria-label={ariaLabel} className={className}>
                <SelectValue />
            </SelectTrigger>
            <SelectContent>
                {emptyLabel !== undefined && (
                    <SelectItem value={GATEWAY_SELECT_EMPTY} className="text-xs!">
                        {emptyLabel}
                    </SelectItem>
                )}
                {options.map((o) => (
                    <SelectItem key={o.value} value={o.value} className="text-xs!">
                        {o.label}
                    </SelectItem>
                ))}
            </SelectContent>
        </Select>
    );
}

export default GatewaySelect;
