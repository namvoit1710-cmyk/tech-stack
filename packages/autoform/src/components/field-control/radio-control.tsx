import { Label } from "@ldc/ui/components/label";
import { RadioGroup, RadioGroupItem } from "@ldc/ui/components/radio-group";
import { forwardRef, useMemo } from "react";
import { useServerOptions } from "../../hooks/use-server-option";
import type { FieldComponentProps } from "../../types/schema";

export interface IRadioOption {
    id: string;
    value: string;
    // SA-1239: optional — options carry {id, value}; display falls back to `value`.
    label?: string;
}

type OptionRecord = Record<string, string | number>;

export interface RadioControlProps extends FieldComponentProps {
    options?: IRadioOption[] | OptionRecord[];
    valueKey?: string;
    labelKey?: string;
    onValueChange?: (value: string) => void;
}

const RadioControl = forwardRef<HTMLDivElement, RadioControlProps>((props, ref) => {
    const {
        field,
        options,
        value,
        onChange,
        onValueChange,
        valueKey = "id",
        labelKey = "value",
        className,
        // Strip the top-level serverOptions prop (spread from controlProps by builder-field)
        // so it never lands on the <RadioGroup> DOM node.
        serverOptions: _serverOptions,
        ...rest
    } = props;

    const { data } = useServerOptions(field?.fieldConfig?.controlProps?.serverOptions);

    const resolvedOptions = useMemo(() => {
        if (field?.fieldConfig?.controlProps?.serverOptions) {
            return data ?? [];
        }
        return Array.isArray(options) ? options : [];
    }, [data, options, field]);

    const handleValueChange = (newValue: string) => {
        onValueChange?.(newValue);
        onChange?.(newValue);
    };

    return (
        <RadioGroup
            ref={ref}
            value={value as string}
            onValueChange={handleValueChange}
            {...rest}
            className={className}
        >
            {resolvedOptions.map((option: IRadioOption | OptionRecord, index: number) => {
                const optionValue = String((option as OptionRecord)[valueKey]);
                const optionLabel = String((option as OptionRecord)[labelKey]);
                const itemId = `radio-${optionValue}-${index}`;

                return (
                    <div key={itemId} className="flex items-center space-x-2">
                        <RadioGroupItem value={optionValue} id={itemId} />
                        <Label htmlFor={itemId}>{optionLabel}</Label>
                    </div>
                );
            })}
        </RadioGroup>
    );
});

RadioControl.displayName = "RadioControl";

export default RadioControl;
