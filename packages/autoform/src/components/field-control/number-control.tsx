import { NumberInput } from "@ldc/ui/components/number-input";
import { forwardRef } from "react";

export interface INumberControlProps extends Omit<React.ComponentProps<typeof NumberInput>, "onChange"> {
    min?: number
    max?: number
    name: string
    readonly?: boolean
    onChange?: (event: { target: { name: string, value: number | undefined } }) => void
}

const NumberControl = forwardRef<HTMLInputElement, INumberControlProps>(
    (props, ref) => {
        const { min, max, onChange, readonly, value, name, disabled, ...rest } = props;

        return (
            <NumberInput
                {...rest}
                ref={ref}
                min={min}
                max={max}
                value={typeof value === "string" ? Number(value) : value}
                disabled={disabled}
                readOnly={readonly}
                onChange={(value) => {
                    onChange?.({ target: { name, value: value } })
                }}
            />
        )
    });

NumberControl.displayName = "NumberControl";

export default NumberControl;