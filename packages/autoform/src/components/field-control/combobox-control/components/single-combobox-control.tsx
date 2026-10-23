import {
    Combobox,
    ComboboxContent,
    ComboboxEmpty,
    ComboboxInput,
    ComboboxItem,
    ComboboxList,
} from "@ldc/ui/components/combobox";
import { Input } from "@ldc/ui/components/input";
import React from "react";
import type { ISingleComboboxControlProps } from "../types/types";

const SingleComboboxControl = React.forwardRef<HTMLInputElement, ISingleComboboxControlProps>(
    ({ name, value, onChange, placeholder, disabled, filteredOptions, inputValue, setInputValue }, _ref) => {
        const selectedLabel = filteredOptions.find((o) => o.id === value)?.value ?? value;

        return (
            <Combobox
                value={value}
                onValueChange={(val) => {
                    setInputValue("");
                    onChange({ target: { name, value: val as string } });
                }}
                disabled={disabled}
            >
                <ComboboxInput
                    showClear={!!value}
                    value={selectedLabel}
                    placeholder={placeholder}
                    readOnly
                    className="w-full"
                />
                <ComboboxContent>
                    <Input
                        value={inputValue}
                        onChange={(e) => setInputValue(e.target.value)}
                        placeholder="Search..."
                        className="m-1 h-8 w-[calc(100%-0.5rem)]"
                        autoFocus
                    />
                    <ComboboxList>
                        <ComboboxEmpty>No results</ComboboxEmpty>
                        {filteredOptions.map((option) => (
                            <ComboboxItem key={option.id} value={option.id}>
                                {option.value}
                            </ComboboxItem>
                        ))}
                    </ComboboxList>
                </ComboboxContent>
            </Combobox>
        );
    }
);

SingleComboboxControl.displayName = "SingleComboboxControl";

export default SingleComboboxControl;
