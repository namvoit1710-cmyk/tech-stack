/* eslint-disable react-hooks/set-state-in-effect */
import { useDebounceCallback } from "@ldc/ui/hooks/use-debounce-callback";
import type { ComponentType } from "react";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { useFormContext } from "react-hook-form";
import { useBuilderContext } from "../../../../contexts/builder.context";
import type { FieldComponentProps, IField } from "../../../../types/schema";


export interface ITableCellFieldProps {
    field: IField;
    value: any;
    name: string;
    rowIndex: number;
    rowId: string;
    fieldKey: string;
    disabled?: boolean;
    onClick?: () => void;
    onCellChange: (id: string, fieldKey: string, value: any) => void;
}

const TableCellField = (props: ITableCellFieldProps) => {
    const { field, value, rowId, fieldKey, onCellChange, onClick, disabled = false, name } = props;
    const { fieldComponent } = useBuilderContext();

    const { getFieldState, formState } = useFormContext();

    // Get the state (error) of this specific cell field
    const { error } = getFieldState(name, formState);

    const fieldControl: string = field.fieldConfig.fieldControl ?? "InputControl";
    const controlProps = field?.fieldConfig?.controlProps || {};
    const FieldComponent: ComponentType<FieldComponentProps> | null = fieldComponent?.[fieldControl] ?? null;

    const [fieldValue, setFieldValue] = useState(value);
    const externalValue = useRef(value);

    const handleChange = useCallback((val: any) => {
        externalValue.current = val;
        onCellChange(rowId, fieldKey, val);
    }, [onCellChange, rowId, fieldKey]);

    const debouncedHandleChange = useDebounceCallback(handleChange, 500);
    const handleFieldChange = (value: any) => {
        if (typeof value === "object" && value !== null && "target" in value) {
            setFieldValue(value.target.value ?? value);
            debouncedHandleChange(value.target.value ?? value);
        } else {
            setFieldValue(value);
            debouncedHandleChange(value);
        }
    };

    useEffect(() => {
        if (externalValue.current !== value) {
            externalValue.current = value;
            setFieldValue(value);

            if (debouncedHandleChange.cancel) {
                debouncedHandleChange.cancel();
            }
        }
    }, [value]);

    if (!FieldComponent) return <span />;

    if (fieldControl === "ButtonControl") {
        return (
            <FieldComponent
                {...controlProps}
                name={name}
                field={field}
                disabled={disabled}
                onClick={onClick}
            />
        );
    }

    return (
        <div className={`w-full min-h-8 `}>
            <FieldComponent
                {...controlProps}
                name={name}
                error={error}
                value={fieldValue ?? ""}
                onChange={handleFieldChange}
                disabled={disabled}
            />
        </div>
    );
};

export default React.memo(TableCellField);
