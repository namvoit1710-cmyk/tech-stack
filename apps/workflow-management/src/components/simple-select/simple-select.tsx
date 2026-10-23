// A native-`<select>`-shaped wrapper over the shared Radix Select from @ldc/ui.
//
// Why this exists: a native `<select>` does not render its own option list — the
// browser does, with the SYSTEM palette. In dark mode that popup came up with a
// white background while each `<option>` inherited the theme foreground, which is
// also white: an invisible list until you arrowed onto a row, and black text on
// the closed trigger afterwards. No amount of Tailwind on the `<select>` fixes
// that, because the popup is not part of our DOM.
//
// Every dropdown in the workflow builder already goes through @ldc/ui's Select
// (autoform's SelectControl), which renders its own popup with
// `bg-popover text-popover-foreground` and is theme-correct in both modes. This
// wrapper keeps the `{value, onValueChange, options}` ergonomics the native call
// sites had, so each of them stays one element instead of five.
import { cn } from "@ldc/ui";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@ldc/ui/components/select";

// Radix reserves the empty string: `<SelectItem value="">` throws, because ""
// is how it represents "nothing selected". A native `<option value="">` is a
// real, selectable row though — it is how every call site here lets a user go
// back to "none". So the row is carried under a sentinel and translated at the
// boundary; "" never crosses into Radix and never leaks out to callers.
const NONE = "__simple_select_none__";

export interface ISimpleSelectOption {
  value: string;
  label: string;
}

export interface ISimpleSelectProps {
  value?: string;
  /** Receives "" when the user picks the `emptyLabel` row. */
  onValueChange: (value: string) => void;
  options: ISimpleSelectOption[];
  /** Renders a real, selectable row that emits "" — the native `<option value="">`. */
  emptyLabel?: string;
  /** Trigger text while nothing is selected. Only reachable without an `emptyLabel` row. */
  placeholder?: string;
  disabled?: boolean;
  /** Applied to the trigger — the element the native `<select>` className styled. */
  className?: string;
  id?: string;
  "aria-label"?: string;
  "data-testid"?: string;
  size?: "sm" | "default";
}

export const SimpleSelect = ({
  value,
  onValueChange,
  options,
  emptyLabel,
  placeholder,
  disabled = false,
  className,
  id,
  "aria-label": ariaLabel,
  "data-testid": testId,
  size = "default",
}: ISimpleSelectProps) => {
  const hasEmptyRow = emptyLabel !== undefined;
  // An option whose value is "" would throw inside Radix. Data-driven lists
  // (gateway apps, server options) can produce one; drop it rather than take
  // the whole panel down with it.
  const items = options.filter(o => o.value !== "");
  const current = value ? value : hasEmptyRow ? NONE : undefined;

  return (
    <Select
      value={current}
      onValueChange={next => onValueChange(next === NONE ? "" : next)}
      disabled={disabled}
    >
      <SelectTrigger
        id={id}
        size={size}
        aria-label={ariaLabel}
        data-testid={testId}
        className={cn("w-full", className)}
      >
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {hasEmptyRow && <SelectItem value={NONE}>{emptyLabel}</SelectItem>}
        {items.map(o => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
};

SimpleSelect.displayName = "SimpleSelect";
