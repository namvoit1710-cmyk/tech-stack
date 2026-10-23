import { cn } from "@ldc/ui";
import { isNil, isObject } from "lodash-es";
import React, { useState } from "react";
import { useWatch } from "react-hook-form";
import { useBuilderContext } from "../../../contexts/builder.context";
import SelectTableDialog from "./components/select-table-dialog";
import SelectTableTrigger from "./components/select-table-trigger";
import { useSelectTableOptions } from "./hooks/use-select-table-options";
import type { ISelectTableControlProps, OptionRecord } from "./types/types";

const SelectTableControl = React.forwardRef<HTMLDivElement, ISelectTableControlProps>(
    (props, ref) => {
        const {
            name,
            value,
            onChange,
            multiple = false,
            options,
            valueKey = "id",
            labelKey = "value",
            placeholder,
            disabled,
            field,
            error: fieldError,
        } = props;

        const serverOptions = field?.fieldConfig?.controlProps?.serverOptions;
        const selectAction = field?.fieldConfig?.controlProps?.selectAction as string | undefined;
        const readOnly = field?.fieldConfig?.controlProps?.readOnly as boolean | undefined;

        const { onFormActions } = useBuilderContext();

        const [open, setOpen] = useState(false);
        const [selectionId, setSelectionId] = useState<string | null>(null);

        const { data, loading, error, totalCount, searchValue, setSearchValue, pagination, setPagination } =
            useSelectTableOptions({
                serverOptions,
                staticOptions: Array.isArray(options) ? options : [],
                enabled: open,
            });

        const handleConfirm = (selected: OptionRecord | OptionRecord[], newSelectionId: string) => {
            onChange?.({ target: { name, value: selected } });
            setSelectionId(newSelectionId || null);

            const record = Array.isArray(selected) ? selected[0] : selected;
            if (selectAction && onFormActions && record != null) {
                const idValue = record[valueKey] as string | number | null | undefined;
                void onFormActions(selectAction, {
                    rowData: record,
                    rowId: idValue != null ? String(idValue) : undefined,
                });
            }
        };

        const handleClear = () => {
            if (readOnly) return;
            onChange?.({ target: { name, value: multiple ? [] : null } });
            setSelectionId(null);
        };

        const isPrimitiveValue = !isNil(value) && !isObject(value);

        const siblingLabel = useWatch({ name: labelKey, disabled: !isPrimitiveValue }) as string | undefined;
        const currentValue = (value as OptionRecord | OptionRecord[] | null) ?? (multiple ? [] : null);

        return (
            <div ref={ref} className={cn(fieldError && "ring-1 ring-destructive rounded-md")}>
                <SelectTableTrigger
                    value={currentValue}
                    multiple={multiple}
                    labelKey={labelKey}
                    labelOverride={isPrimitiveValue ? (siblingLabel ?? String(value)) : undefined}
                    placeholder={placeholder}
                    disabled={disabled}
                    onOpen={() => { if (!disabled && !readOnly) setOpen(true); }}
                    onClear={handleClear}
                />

                <SelectTableDialog
                    open={open}
                    onOpenChange={setOpen}
                    multiple={multiple}
                    valueKey={valueKey}
                    labelKey={labelKey}
                    data={data}
                    loading={loading}
                    error={error}
                    totalCount={totalCount}
                    pagination={pagination}
                    onPaginationChange={setPagination}
                    searchValue={searchValue}
                    onSearchChange={setSearchValue}
                    currentValue={currentValue}
                    onConfirm={handleConfirm}
                    selectionId={selectionId}
                />
            </div>
        );
    }
);

SelectTableControl.displayName = "SelectTableControl";

export default SelectTableControl;
