import React from "react";
import MultipleComboboxControl from "./components/multiple-combobox-control";
import SingleComboboxControl from "./components/single-combobox-control";
import { useComboboxOptions } from "./hooks/use-combobox-options";
import type { IComboboxControlProps } from "./types/types";

const ComboboxControl = React.forwardRef<HTMLInputElement | HTMLDivElement, IComboboxControlProps>(
    (props, ref) => {
        const {
            name,
            value,
            onChange,
            multiple,
            options,
            valueKey,
            labelKey,
            placeholder,
            disabled,
            field,
        } = props;

        const serverOptions = field?.fieldConfig?.controlProps?.serverOptions;

        const { filteredOptions, inputValue, setInputValue } = useComboboxOptions({
            options,
            valueKey,
            labelKey,
            serverOptions,
        });

        if (multiple) {
            return (
                <MultipleComboboxControl
                    ref={ref}
                    name={name}
                    value={(value as string[]) ?? []}
                    onChange={onChange as (e: { target: { name: string; value: string[] } }) => void}
                    placeholder={placeholder}
                    disabled={disabled}
                    filteredOptions={filteredOptions}
                    inputValue={inputValue}
                    setInputValue={setInputValue}
                />
            );
        }

        return (
            <SingleComboboxControl
                ref={ref as React.Ref<HTMLInputElement>}
                name={name}
                value={(value as string) ?? ""}
                onChange={onChange as (e: { target: { name: string; value: string } }) => void}
                placeholder={placeholder}
                disabled={disabled}
                filteredOptions={filteredOptions}
                inputValue={inputValue}
                setInputValue={setInputValue}
            />
        );
    }
);

ComboboxControl.displayName = "ComboboxControl";

export default ComboboxControl;
