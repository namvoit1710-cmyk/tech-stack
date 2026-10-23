import { Input } from "@ldc/ui/components/input";
import { forwardRef, useCallback } from "react";
import { useFieldScript } from "../../libs/action-engine/script";
import type { FieldComponentProps } from "../../types/schema";

const InputControl = forwardRef<HTMLInputElement, FieldComponentProps>((props, ref) => {
    const { field, error, onChange, onInput, onFocus, onBlur, ...rest } = props;
    const runFieldScript = useFieldScript();
    const handleChange = useCallback(
        (e: React.ChangeEvent<HTMLInputElement>) => {
            onChange?.(e);
            if (field) runFieldScript(field, "onChange", { value: e.target.value, name: e.target.name });
        },
        [onChange, field, runFieldScript],
    );
    const handleInput = useCallback(
        (e: React.FormEvent<HTMLInputElement>) => {
            onInput?.(e);
            if (field) runFieldScript(field, "onInput", { value: (e.target as HTMLInputElement).value });
        },
        [onInput, field, runFieldScript],
    );
    const handleFocus = useCallback(
        (e: React.FocusEvent<HTMLInputElement>) => {
            onFocus?.(e);
            if (field) runFieldScript(field, "onFocus", { name: e.target.name });
        },
        [onFocus, field, runFieldScript],
    );
    const handleBlur = useCallback(
        (e: React.FocusEvent<HTMLInputElement>) => {
            onBlur?.(e);
            if (field) runFieldScript(field, "onBlur", { name: e.target.name, value: e.target.value });
        },
        [onBlur, field, runFieldScript],
    );
    return (
        <Input
            ref={ref}
            aria-invalid={!!error}
            {...rest}
            onChange={handleChange}
            onInput={handleInput}
            onFocus={handleFocus}
            onBlur={handleBlur}
        />
    );
});

InputControl.displayName = "InputControl";

export default InputControl;