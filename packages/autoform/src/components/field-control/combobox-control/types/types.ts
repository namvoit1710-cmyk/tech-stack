import type { FieldComponentProps } from "../../../../types/schema";

export interface IComboboxOption {
    id: string;
    value: string;
}

export type OptionRecord = Record<string, string | number>;

export interface IComboboxControlProps extends FieldComponentProps {
    multiple?: boolean;
    options?: IComboboxOption[] | OptionRecord[];
    valueKey?: string;
    labelKey?: string;
    placeholder?: string;
    onChange?: (event: { target: { name: string; value: string | string[] } }) => void;
}

export interface ISubComboboxControlProps {
    name: string;
    placeholder?: string;
    disabled?: boolean;
    filteredOptions: IComboboxOption[];
    inputValue: string;
    setInputValue: (v: string) => void;
}

export interface ISingleComboboxControlProps extends ISubComboboxControlProps {
    value: string;
    onChange: (event: { target: { name: string; value: string } }) => void;
}

export interface IMultipleComboboxControlProps extends ISubComboboxControlProps {
    value: string[];
    onChange: (event: { target: { name: string; value: string[] } }) => void;
}
