import {
    Combobox,
    ComboboxChip,
    ComboboxChips,
    ComboboxContent,
    ComboboxEmpty,
    ComboboxItem,
    ComboboxList,
    useComboboxAnchor,
} from "@ldc/ui/components/combobox";
import { Input } from "@ldc/ui/components/input";
import { ChevronDownIcon } from "lucide-react";
import React from "react";
import type { IMultipleComboboxControlProps } from "../types/types";

const MultipleComboboxControl = React.forwardRef<HTMLDivElement, IMultipleComboboxControlProps>(
    ({ name, value, onChange, placeholder, disabled, filteredOptions, inputValue, setInputValue }, _ref) => {
        const anchor = useComboboxAnchor();

        const getLabelById = (id: string) =>
            filteredOptions.find((o) => o.id === id)?.value ?? id;

        return (
            <Combobox
                multiple
                value={value}
                onValueChange={(val) => {
                    onChange({ target: { name, value: val } });
                }}
                disabled={disabled}
            >
                <ComboboxChips ref={anchor} className="w-full min-h-10 cursor-pointer">
                    {value.map((id) => (
                        <ComboboxChip key={id}>
                            {getLabelById(id)}
                        </ComboboxChip>
                    ))}
                    {value.length === 0 && (
                        <span className="text-sm text-muted-foreground px-1">{placeholder}</span>
                    )}
                    <ChevronDownIcon className="ml-auto size-4 text-muted-foreground shrink-0" />
                </ComboboxChips>
                <ComboboxContent anchor={anchor}>
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

MultipleComboboxControl.displayName = "MultipleComboboxControl";

export default MultipleComboboxControl;
