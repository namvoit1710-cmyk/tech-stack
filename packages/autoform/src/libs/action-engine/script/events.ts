export type EventName = "onChange" | "onInput" | "onFocus" | "onBlur" | "onClick";

/**
 * Control (by fieldControl key) -> events that may carry a script. Extensible per phase.
 * P1 wires InputControl + ButtonControl at runtime; the rest are declared for the Builder UI.
 */
export const CONTROL_EVENTS: Record<string, readonly EventName[]> = {
    InputControl: ["onChange", "onInput", "onFocus", "onBlur"],
    TextareaControl: ["onChange", "onInput", "onFocus", "onBlur"],
    NumberControl: ["onChange", "onInput", "onFocus", "onBlur"],
    SelectControl: ["onChange"],
    RadioPopupControl: ["onChange"],
    SwitchControl: ["onChange"],
    CheckBoxControl: ["onChange"],
    ButtonControl: ["onClick"],
};

export function isKnownEvent(control: string, event: string): boolean {
    const events = CONTROL_EVENTS[control];
    return !!events && (events as readonly string[]).includes(event);
}
