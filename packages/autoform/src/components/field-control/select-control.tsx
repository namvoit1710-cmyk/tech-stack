import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@ldc/ui/components/select";
import React, { useMemo } from "react";
import { useServerOptions } from "../../hooks/use-server-option";
import type { FieldComponentProps } from "../../types/schema";

export interface ISelectOption {
    id: string;
    value: string;
}

type OptionRecord = Record<string, string | number>;

export interface ISelectControlProps extends FieldComponentProps {
    valueKey?: string;
    labelKey?: string;
    onChange?: (event: { target: { name: string; value: string } }) => void;
    options?: ISelectOption[] | OptionRecord[];
}

const SelectControl = React.forwardRef<HTMLButtonElement, ISelectControlProps>(
    (props, ref) => {
        const {
            name,
            options,
            value,
            onChange,
            valueKey,
            labelKey,
            placeholder,
            error,
            disabled,
        } = props;

        const { data } = useServerOptions(props.field?.fieldConfig?.controlProps?.serverOptions);

        const _options = useMemo(() => {
            if (props.field?.fieldConfig?.controlProps?.serverOptions) {
                return data ?? [];
            }

            return Array.isArray(options) ? options : [];
        }, [data, options, props.field]);

        return (
            <Select
                value={value as string | undefined}
                onValueChange={(value) => onChange?.({ target: { name, value } })}
                name={name}
                disabled={disabled}
            >
                <SelectTrigger ref={ref} size="default" className="w-full h-10!">
                    <SelectValue
                        placeholder={placeholder as string}
                        data-invalid={!!error}
                    />
                </SelectTrigger>
                <SelectContent position="popper">
                    {_options.map((option: ISelectOption | OptionRecord, index: number) => {
                        const optionValue = String((option as OptionRecord)[valueKey ?? "id"]);
                        const optionLabel = String((option as OptionRecord)[labelKey ?? "value"]);
                        const keyValue = 'id' in option ? option.id : optionValue;
                        return (
                            <SelectItem key={`${keyValue}-${index}`} value={optionValue}>
                                {optionLabel}
                            </SelectItem>
                        );
                    })}
                </SelectContent>
            </Select>
        );
    }
);

SelectControl.displayName = "SelectControl";

export default SelectControl;
