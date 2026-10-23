/** Controls whose single-value output type is coupled to their `multiple` flag. */
const SINGLE_OUTPUT: Record<string, string> = {
    ComboboxControl: "string",
    SelectTableControl: "object",
};

/**
 * Date/DateTime controls whose `mode` (single|range) is coupled to outputType: range → the
 * field outputs {from,to} (object), single → a scalar string. Time is deliberately excluded —
 * its `mode` is a 12h/24h FORMAT, not a selection shape (SA-2038 Task 4).
 */
const RANGE_COUPLED = new Set(["DatePickerControl", "DateTimePickerControl"]);

/**
 * The node outputType a Choice control should carry given its controlProps.
 * `multiple: true` → "array"; single → the control's own scalar output; a control that
 * is not multiple-coupled keeps `fallback` (the node's current outputType).
 *
 * Date/DateTime `mode` is coupled separately (RANGE_COUPLED): `range` → "object", `single` →
 * "string" — checked before the Choice `multiple` coupling since the two coupling sets are
 * mutually exclusive per control.
 */
export function outputTypeForControlProps(
    controlKey: string,
    controlProps: Record<string, unknown>,
    fallback: string
): string {
    if (RANGE_COUPLED.has(controlKey)) return controlProps.mode === "range" ? "object" : "string";
    const single = SINGLE_OUTPUT[controlKey];
    if (single === undefined) return fallback;
    return controlProps.multiple === true ? "array" : single;
}
